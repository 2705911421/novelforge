"""Runtime event persistence and product-level event translation."""

from __future__ import annotations

import json
from typing import Any

from src.core.database import Database

from .contracts import AgentTask, DomainEvent, RuntimeEvent, UIEvent


class RuntimeEventTranslator:
    """Translate vendor-shaped events without leaking them into the UI."""

    _MAP = {
        "tool.started": ("agent.tool.started", "agent.tool.started", "正在执行智能工具"),
        "tool.completed": ("agent.tool.completed", "agent.tool.completed", "智能工具已完成"),
        "tool.call.started": ("agent.tool.call.started", "agent.tool.started", "正在调用 NovelForge 工具"),
        "tool.call.completed": ("agent.tool.call.completed", "agent.tool.completed", "NovelForge 工具调用完成"),
        "tool.call.failed": ("agent.tool.call.failed", "agent.error", "NovelForge 工具调用被拒绝"),
        "approval.denied": ("agent.approval.denied", "agent.approval", "运行时权限请求已拒绝"),
        "runtime.request.rejected": ("agent.runtime.request.rejected", "agent.error", "运行时请求不在宿主契约内"),
        "turn.started": ("agent.turn.started", "agent.progress", "Agent 正在执行"),
        "turn.completed": ("agent.turn.completed", "agent.progress", "Agent 本轮执行完成"),
        "turn.failed": ("agent.turn.failed", "agent.error", "Agent 本轮执行失败"),
        "turn.cancelled": ("agent.turn.cancelled", "agent.error", "Agent 本轮已取消"),
        "thread.started": ("agent.thread.started", "agent.progress", "已建立 Agent 执行上下文"),
        "item.started": ("agent.item.started", "agent.progress", "Agent 开始处理执行项"),
        "item.completed": ("agent.item.completed", "agent.progress", "Agent 执行项已完成"),
        "item/started": ("agent.item.started", "agent.progress", "Agent 开始处理执行项"),
        "item/completed": ("agent.item.completed", "agent.progress", "Agent 执行项已完成"),
        "turn.delta": ("agent.turn.delta", "agent.progress", "Agent 正在生成内容"),
        "message.delta": ("agent.message.delta", "agent.progress", "Agent 正在生成内容"),
        "approval.requested": ("agent.approval.requested", "agent.approval", "Agent 等待批准"),
        "error": ("agent.runtime.error", "agent.error", "智能运行时报告错误"),
    }

    def translate(self, event: RuntimeEvent, task: AgentTask) -> DomainEvent:
        payload = dict(event.payload)
        event_type = event.event_type.replace("/", ".")
        domain_type, ui_type, message = self._MAP.get(
            event_type,
            ("agent.runtime.event", "agent.progress", "Agent 正在执行"),
        )
        if event_type in {"tool.started", "tool.completed"}:
            tool_name = self._tool_name(payload)
            if tool_name in {"memory.search", "context.memory.search", "search.memory"}:
                if event_type == "tool.started":
                    domain_type, ui_type, message = (
                        "context.memory.search.started",
                        "agent.progress",
                        "正在检索相关记忆",
                    )
                else:
                    domain_type, ui_type = "context.memory.search.completed", "agent.progress"
                    count = self._evidence_count(payload)
                    if count is not None:
                        payload["evidenceCount"] = count
                        message = f"已加载 {count} 条相关记忆"
                    else:
                        message = "相关记忆加载完成"
        payload.update({
            "taskId": task.task_id,
            "runtimeType": event.runtime_type,
            "runtimeEventType": event.event_type,
        })
        return DomainEvent(
            event_type=domain_type,
            payload=payload,
            ui_type=ui_type,
            ui_message=message,
            agent_run_id=event.agent_run_id,
            sequence=event.sequence,
        )

    @staticmethod
    def _tool_name(payload: dict[str, Any]) -> str:
        for key in ("toolName", "tool_name", "tool", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower().replace("/", ".").replace("_", ".")
        return ""

    @staticmethod
    def _evidence_count(payload: dict[str, Any]) -> int | None:
        for key in ("evidenceCount", "evidence_count", "resultCount", "result_count", "count"):
            value = payload.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return max(0, value)
        return None


class RuntimeEventStore:
    """Persist raw runtime events and their safe domain/UI projection."""

    def __init__(self, db: Database, translator: RuntimeEventTranslator | None = None):
        self.db = db
        self.translator = translator or RuntimeEventTranslator()

    def append(self, event: RuntimeEvent, task: AgentTask) -> DomainEvent:
        with self.db.transaction() as conn:
            return self.append_in_transaction(conn, event, task)

    def append_in_transaction(self, conn, event: RuntimeEvent, task: AgentTask) -> DomainEvent:
        """Append raw and translated events to a caller-owned transaction.

        Recovery and other durable state transitions need task, run, and event
        updates to commit together.  Keeping this seam explicit avoids nested
        SQLite transactions while preserving the same translation path.
        """
        previous = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM runtime_events WHERE agent_run_id IS ?",
            (event.agent_run_id,),
        ).fetchone()
        sequence = max(int(previous["sequence"] or 0) + 1, int(event.sequence or 0))
        runtime_event = RuntimeEvent(
            runtime_type=event.runtime_type,
            event_type=event.event_type,
            payload=dict(event.payload),
            sequence=sequence,
            agent_run_id=event.agent_run_id,
            timestamp=event.timestamp,
        )
        cursor = conn.execute(
            """INSERT INTO runtime_events(agent_run_id, sequence, runtime_type, event_type, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                runtime_event.agent_run_id,
                sequence,
                runtime_event.runtime_type,
                runtime_event.event_type,
                json.dumps(runtime_event.payload, ensure_ascii=False, default=str),
                runtime_event.timestamp,
            ),
        )
        runtime_event_id = int(cursor.lastrowid or 0)
        domain = self.translator.translate(runtime_event, task)
        domain_payload = dict(domain.payload)
        domain_payload["runtimeEventId"] = runtime_event_id
        conn.execute(
            """INSERT INTO domain_events(
                   agent_run_id, sequence, event_type, payload, ui_type, ui_message, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                domain.agent_run_id,
                sequence,
                domain.event_type,
                json.dumps(domain_payload, ensure_ascii=False, default=str),
                domain.ui_type,
                domain.ui_message,
                runtime_event.timestamp,
            ),
        )
        return DomainEvent(
            event_type=domain.event_type,
            payload=domain_payload,
            ui_type=domain.ui_type,
            ui_message=domain.ui_message,
            agent_run_id=domain.agent_run_id,
            sequence=sequence,
        )

    def domain_events(self, agent_run_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM domain_events WHERE agent_run_id=? AND sequence>? ORDER BY sequence, id",
            (agent_run_id, after_sequence),
        )
        for row in rows:
            try:
                row["payload"] = json.loads(row.get("payload") or "{}")
            except json.JSONDecodeError:
                row["payload"] = {}
        return rows

    def ui_events(self, agent_run_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        """Read the UI projection without exposing vendor-shaped events."""
        events: list[dict[str, Any]] = []
        for row in self.domain_events(agent_run_id, after_sequence=after_sequence):
            payload = row.get("payload")
            events.append(
                UIEvent(
                    ui_type=str(row.get("ui_type") or "agent.progress"),
                    message=str(row.get("ui_message") or "正在执行任务"),
                    payload=payload if isinstance(payload, dict) else {},
                    agent_run_id=agent_run_id,
                    sequence=int(row.get("sequence") or 0),
                ).to_dict()
            )
        return events
