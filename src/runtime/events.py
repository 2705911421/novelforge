"""Runtime event persistence and product-level event translation."""

from __future__ import annotations

import json
from typing import Any

from src.core.database import Database

from .contracts import AgentTask, DomainEvent, RuntimeEvent


class RuntimeEventTranslator:
    """Translate vendor-shaped events without leaking them into the UI."""

    _MAP = {
        "tool.started": ("agent.tool.started", "agent.tool.started", "正在执行智能工具"),
        "tool.completed": ("agent.tool.completed", "agent.tool.completed", "智能工具已完成"),
        "turn.started": ("agent.turn.started", "agent.progress", "Agent 正在执行"),
        "turn.completed": ("agent.turn.completed", "agent.progress", "Agent 本轮执行完成"),
        "thread.started": ("agent.thread.started", "agent.progress", "已建立 Agent 执行上下文"),
        "error": ("agent.runtime.error", "agent.error", "智能运行时报告错误"),
    }

    def translate(self, event: RuntimeEvent, task: AgentTask) -> DomainEvent:
        domain_type, ui_type, message = self._MAP.get(
            event.event_type,
            ("agent.runtime.event", "agent.progress", "Agent 正在执行"),
        )
        payload = dict(event.payload)
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


class RuntimeEventStore:
    """Persist raw runtime events and their safe domain/UI projection."""

    def __init__(self, db: Database, translator: RuntimeEventTranslator | None = None):
        self.db = db
        self.translator = translator or RuntimeEventTranslator()

    def append(self, event: RuntimeEvent, task: AgentTask) -> DomainEvent:
        with self.db.transaction() as conn:
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
