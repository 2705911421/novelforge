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
        "recovery.started": ("agent.recovery.started", "agent.progress", "正在从 NovelForge 检查点恢复"),
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
        if tool_name := self._tool_name(payload):
            if tool_name in {"request.more.context", "context.request.more", "context.need.more.context"}:
                if event_type in {"tool.call.failed", "tool.failed"}:
                    domain_type, ui_type, message = (
                        "context.need_more_context.failed",
                        "agent.error",
                        "宿主上下文补充请求失败",
                    )
                elif event_type in {"tool.call.started", "tool.started"}:
                    domain_type, ui_type, message = (
                        "context.need_more_context.started",
                        "agent.progress",
                        "正在向宿主请求补充上下文",
                    )
                else:
                    domain_type, ui_type, message = (
                        "context.need_more_context.completed",
                        "agent.progress",
                        "宿主已返回受控上下文补充",
                    )
                output = payload.get("output")
                if isinstance(output, dict):
                    request = output.get("request")
                    if isinstance(request, dict):
                        payload["contextRequest"] = {
                            "type": request.get("type"),
                            "sections": list(request.get("sections") or []),
                        }
                    provided = output.get("provided")
                    if isinstance(provided, dict):
                        payload["contextProvidedSections"] = sorted(str(key) for key in provided)
                    denied = output.get("denied")
                    if isinstance(denied, dict):
                        payload["contextDeniedSections"] = sorted(str(key) for key in denied)
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

    _COMPACTABLE_EVENT_TYPES = frozenset({
        "turn.delta",
        "message.delta",
        "item.delta",
    })
    _MAX_COMPACTED_TEXT_CHARS = 64_000

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
            """SELECT id, sequence, event_type, payload
               FROM runtime_events
               WHERE agent_run_id IS ?
               ORDER BY sequence DESC, id DESC LIMIT 1""",
            (event.agent_run_id,),
        ).fetchone()
        previous_sequence = int(previous["sequence"] or 0) if previous else 0
        sequence = max(previous_sequence + 1, int(event.sequence or 0))
        # A stream without an AgentRun cannot be safely attributed to one
        # task, so leave those events append-only rather than coalescing across
        # unrelated callers.
        compacted = event.agent_run_id is not None and self._should_compact(previous, event.event_type)
        payload = dict(event.payload)
        if compacted:
            payload = self._compact_payload(self._decode_payload(previous["payload"]), payload)
        runtime_event = RuntimeEvent(
            runtime_type=event.runtime_type,
            event_type=event.event_type,
            payload=payload,
            sequence=sequence,
            agent_run_id=event.agent_run_id,
            timestamp=event.timestamp,
        )
        serialized_payload = json.dumps(runtime_event.payload, ensure_ascii=False, default=str)
        if compacted:
            runtime_event_id = int(previous["id"])
            conn.execute(
                """UPDATE runtime_events
                   SET sequence=?, runtime_type=?, event_type=?, payload=?, created_at=?
                   WHERE id=?""",
                (
                    sequence,
                    runtime_event.runtime_type,
                    runtime_event.event_type,
                    serialized_payload,
                    runtime_event.timestamp,
                    runtime_event_id,
                ),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO runtime_events(agent_run_id, sequence, runtime_type, event_type, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    runtime_event.agent_run_id,
                    sequence,
                    runtime_event.runtime_type,
                    runtime_event.event_type,
                    serialized_payload,
                    runtime_event.timestamp,
                ),
            )
            runtime_event_id = int(cursor.lastrowid or 0)
        domain = self.translator.translate(runtime_event, task)
        domain_payload = dict(domain.payload)
        domain_payload["runtimeEventId"] = runtime_event_id
        serialized_domain_payload = json.dumps(domain_payload, ensure_ascii=False, default=str)
        if compacted:
            updated = conn.execute(
                """UPDATE domain_events
                   SET sequence=?, event_type=?, payload=?, ui_type=?, ui_message=?, created_at=?
                   WHERE agent_run_id IS ? AND sequence=?""",
                (
                    sequence,
                    domain.event_type,
                    serialized_domain_payload,
                    domain.ui_type,
                    domain.ui_message,
                    runtime_event.timestamp,
                    domain.agent_run_id,
                    previous_sequence,
                ),
            )
            if updated.rowcount != 1:
                self._insert_domain_event(
                    conn, domain, sequence, serialized_domain_payload, runtime_event.timestamp,
                )
        else:
            self._insert_domain_event(
                conn, domain, sequence, serialized_domain_payload, runtime_event.timestamp,
            )
        return DomainEvent(
            event_type=domain.event_type,
            payload=domain_payload,
            ui_type=domain.ui_type,
            ui_message=domain.ui_message,
            agent_run_id=domain.agent_run_id,
            sequence=sequence,
        )

    @classmethod
    def _should_compact(cls, previous: Any, event_type: str) -> bool:
        if not previous:
            return False
        current_type = str(event_type or "").replace("/", ".").strip().lower()
        previous_type = str(previous["event_type"] or "").replace("/", ".").strip().lower()
        return current_type in cls._COMPACTABLE_EVENT_TYPES and current_type == previous_type

    @classmethod
    def _compact_payload(cls, previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        """Bound consecutive stream chunks while retaining useful audit evidence."""
        payload = dict(current)
        try:
            count = max(1, int(previous.get("compactedCount") or 1)) + 1
        except (TypeError, ValueError):
            count = 2
        payload["compacted"] = True
        payload["compactedCount"] = count
        for key in ("delta", "text", "content", "value", "message"):
            old_value = previous.get(key)
            new_value = current.get(key)
            if not isinstance(old_value, str) or not isinstance(new_value, str):
                continue
            combined = old_value + new_value
            if len(combined) > cls._MAX_COMPACTED_TEXT_CHARS:
                combined = combined[:cls._MAX_COMPACTED_TEXT_CHARS]
                payload["truncated"] = True
            payload[key] = combined
        payload["compactedChars"] = sum(
            len(value) for value in payload.values() if isinstance(value, str)
        )
        return payload

    @staticmethod
    def _decode_payload(raw: Any) -> dict[str, Any]:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _insert_domain_event(conn, domain, sequence: int, payload: str, timestamp: str) -> None:
        conn.execute(
            """INSERT INTO domain_events(
                   agent_run_id, sequence, event_type, payload, ui_type, ui_message, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                domain.agent_run_id,
                sequence,
                domain.event_type,
                payload,
                domain.ui_type,
                domain.ui_message,
                timestamp,
            ),
        )

    def domain_events(self, agent_run_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM domain_events WHERE agent_run_id=? AND sequence>? ORDER BY sequence, id",
            (agent_run_id, after_sequence),
        )
        for row in rows:
            row["payload"] = self._decode_payload(row.get("payload"))
        return rows

    def domain_events_for_task(
        self,
        task_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Read product events for a durable task in one global row order.

        AgentRun sequence numbers restart for every run.  The domain-event
        row id is the durable cross-run cursor, so a task with retries or
        multiple role-specific runs can be streamed without skipping events.
        """
        bounded_limit = max(1, min(1000, int(limit)))
        rows = self.db.fetchall(
            """SELECT de.* FROM domain_events AS de
               JOIN agent_runs AS ar ON ar.id=de.agent_run_id
               WHERE ar.task_id=? AND de.id>?
               ORDER BY de.id
               LIMIT ?""",
            (task_id, int(after_id), bounded_limit),
        )
        for row in rows:
            row["payload"] = self._decode_payload(row.get("payload"))
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

    def ui_events_for_task(
        self,
        task_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Project task-scoped DomainEvents into safe UI events with cursors."""
        result: list[dict[str, Any]] = []
        for row in self.domain_events_for_task(task_id, after_id=after_id, limit=limit):
            event = UIEvent(
                ui_type=str(row.get("ui_type") or "agent.progress"),
                message=str(row.get("ui_message") or "正在执行任务"),
                payload=row["payload"] if isinstance(row["payload"], dict) else {},
                agent_run_id=row.get("agent_run_id"),
                sequence=int(row.get("sequence") or 0),
            ).to_dict()
            event.update({
                "taskId": task_id,
                "eventId": int(row["id"]),
                "eventType": row.get("event_type"),
                "createdAt": row.get("created_at"),
            })
            result.append(event)
        return result

    @staticmethod
    def _decode_payload(raw: Any) -> dict[str, Any]:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
