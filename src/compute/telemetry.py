"""Read-only compute telemetry derived from durable AgentRun records.

The first implementation intentionally stays rule/data driven.  It exposes a
stable observation shape for future adaptive routing without adding another
mutable source of truth or requiring a schema migration.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any, Mapping

from src.core.database import Database


def _load(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _number(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
    return None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


class ComputeTelemetryStore:
    """Build adaptive-routing observations from existing durable ledgers."""

    def __init__(self, db: Database):
        self.db = db

    def records(self, *, limit: int = 200, task_type: str | None = None) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 1_000))
        query = """
            SELECT ar.*, at.task_type AS agent_task_type, at.role AS agent_role,
                   at.project_id AS agent_project_id, t.result AS task_result
            FROM agent_runs ar
            JOIN agent_tasks at ON at.id=ar.agent_task_id
            LEFT JOIN tasks t ON t.id=ar.task_id
        """
        params: list[Any] = []
        if task_type:
            query += " WHERE at.task_type=?"
            params.append(task_type)
        query += " ORDER BY ar.started_at DESC, ar.id DESC LIMIT ?"
        params.append(bounded_limit)
        try:
            rows = self.db.fetchall(query, params)
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
        return [self._record(row) for row in rows]

    def snapshot(self, *, limit: int = 200, task_type: str | None = None) -> dict[str, Any]:
        observations = self.records(limit=limit, task_type=task_type)
        aggregate: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "runs": 0,
                "created": 0,
                "running": 0,
                "paused": 0,
                "succeeded": 0,
                "failed": 0,
                "interrupted": 0,
                "cancelled": 0,
                "estimatedCost": 0.0,
                "actualCost": 0.0,
                "latencyMsTotal": 0.0,
                "latencySamples": 0,
                "qualityScoreTotal": 0.0,
                "qualitySamples": 0,
                "gatePass": 0,
                "gateFail": 0,
                "escalated": 0,
            }
        )
        for observation in observations:
            key = (
                str(observation["taskType"]),
                str(observation["runtimeType"]),
                str(observation["modelId"]),
                str(observation["reasoning"]),
            )
            item = aggregate[key]
            item.update({
                "taskType": key[0],
                "runtimeType": key[1],
                "modelId": key[2],
                "reasoning": key[3],
                "capabilityDimension": observation.get("capabilityDimension"),
            })
            item["runs"] += 1
            status = str(observation.get("status") or "unknown")
            if status in item:
                item[status] += 1
            item["estimatedCost"] += float(observation.get("estimatedCost") or 0)
            item["actualCost"] += float(observation.get("actualCost") or 0)
            latency = observation.get("latencyMs")
            if latency is not None:
                item["latencyMsTotal"] += float(latency)
                item["latencySamples"] += 1
            quality = observation.get("qualityScore")
            if quality is not None:
                item["qualityScoreTotal"] += float(quality)
                item["qualitySamples"] += 1
            gate = str(observation.get("gateStatus") or "").upper()
            if gate in {"PASS", "PASSED", "VERIFIED", "COMMITTED"}:
                item["gatePass"] += 1
            elif gate in {"FAIL", "FAILED", "REJECTED"}:
                item["gateFail"] += 1
            if observation.get("escalated"):
                item["escalated"] += 1

        summary: list[dict[str, Any]] = []
        for item in aggregate.values():
            runs = max(1, int(item["runs"]))
            latency_samples = int(item.pop("latencySamples"))
            quality_samples = int(item.pop("qualitySamples"))
            item["successRate"] = round(float(item["succeeded"]) / runs, 4)
            item["avgLatencyMs"] = round(item.pop("latencyMsTotal") / latency_samples, 2) if latency_samples else None
            item["avgQualityScore"] = round(item.pop("qualityScoreTotal") / quality_samples, 2) if quality_samples else None
            for field in ("estimatedCost", "actualCost"):
                item[field] = round(float(item[field]), 4)
            summary.append(item)
        summary.sort(key=lambda item: (item["taskType"], item["runtimeType"], item["modelId"], item["reasoning"]))
        return {
            "observations": observations,
            "summary": summary,
            "source": "agent_runs + compute_plans + tasks",
            "adaptiveRouting": "read-only evidence; scheduler remains rule-driven",
        }

    @staticmethod
    def _record(row: Mapping[str, Any]) -> dict[str, Any]:
        plan = _load(row.get("compute_plan"))
        usage = _load(row.get("usage"))
        artifacts = _load(row.get("artifacts"))
        task_result = _load(row.get("task_result"))
        quality = _number(
            _first(task_result, "qualityScore", "quality_score"),
            _first(artifacts, "qualityScore", "quality_score"),
        )
        gate = _first(
            task_result, "gateStatus", "gate_status", "qualityGate", "quality_gate", "gate",
        )
        if gate is None:
            gate = _first(artifacts, "gateStatus", "gate_status", "qualityGate", "quality_gate", "gate")
        rationale = plan.get("rationale") if isinstance(plan.get("rationale"), list) else []
        escalated = any(str(item).lower().startswith("escalated") for item in rationale)
        return {
            "agentRunId": row.get("id"),
            "agentTaskId": row.get("agent_task_id"),
            "durableTaskId": row.get("task_id"),
            "taskType": row.get("agent_task_type") or "unknown",
            "role": row.get("agent_role") or "unknown",
            "projectId": row.get("agent_project_id"),
            "runtimeType": row.get("runtime_type") or "unknown",
            "modelId": row.get("model_id") or "unknown",
            "reasoning": row.get("reasoning") or plan.get("reasoning") or "unknown",
            "capability": plan.get("capability"),
            "capabilityDimension": plan.get("capabilityDimension"),
            "taskTier": plan.get("taskTier"),
            "difficulty": plan.get("difficulty"),
            "risk": plan.get("risk"),
            "contextSize": plan.get("contextBudget"),
            "estimatedCost": _number(plan.get("estimatedCost")) or 0.0,
            "actualCost": _number(
                _first(usage, "computeUnits", "compute_units", "actualCost", "actual_cost", "cost", "totalCost")
            ) or 0.0,
            "latencyMs": _number(_first(usage, "latencyMs", "latency_ms")),
            "qualityScore": quality,
            "gateStatus": gate,
            "escalated": escalated,
            "criticalFloor": bool(plan.get("criticalFloor")),
            "status": row.get("status") or "unknown",
            "startedAt": row.get("started_at"),
            "finishedAt": row.get("finished_at"),
        }
