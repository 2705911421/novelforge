"""Durable exactly-once boundary for provider generation requests.

The provider itself cannot be made transactional with SQLite.  This module
records the request before dispatch and persists the response before the
surrounding ``GenerationRun`` is marked complete, so a worker restart can
consume a response without issuing a second provider call.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from src.llm.gateway import LLMResponse


RESPONSE_STATUSES = {"response_received", "persisted", "consumed"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def response_artifact(response: LLMResponse) -> dict[str, Any]:
    return {
        "content": str(response.content or ""),
        "model": response.model,
        "provider": response.provider,
        "tokens_used": int(response.tokens_used or 0),
        "prompt_tokens": int(response.prompt_tokens or 0),
        "completion_tokens": int(response.completion_tokens or 0),
        "finish_reason": response.finish_reason,
        "latency_ms": int(response.latency_ms or 0),
    }


def response_from_artifact(artifact: Any) -> LLMResponse:
    value = artifact if isinstance(artifact, dict) else {}
    return LLMResponse(
        content=str(value.get("content") or ""),
        model=str(value.get("model") or ""),
        provider=str(value.get("provider") or ""),
        tokens_used=int(value.get("tokens_used") or 0),
        prompt_tokens=int(value.get("prompt_tokens") or 0),
        completion_tokens=int(value.get("completion_tokens") or 0),
        finish_reason=str(value.get("finish_reason") or ""),
        latency_ms=int(value.get("latency_ms") or 0),
    )


class GenerationAttemptStore:
    """Persistence seam for prepared/requesting/consumed attempts."""

    def __init__(self, db: Any):
        self.db = db

    @staticmethod
    def _decode(row: Any) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        result = dict(row)
        for field in ("response_artifact", "usage"):
            value = result.get(field)
            try:
                result[field] = json.loads(value) if value else {}
            except (TypeError, json.JSONDecodeError):
                result[field] = {}
        return result

    def get(self, attempt_id: str) -> Optional[dict[str, Any]]:
        return self._decode(self.db.fetchone("SELECT * FROM generation_attempts WHERE id=?", (attempt_id,)))

    def by_idempotency(self, key: str) -> Optional[dict[str, Any]]:
        return self._decode(
            self.db.fetchone("SELECT * FROM generation_attempts WHERE idempotency_key=?", (key,))
        )

    def for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM generation_attempts WHERE task_id=? ORDER BY created_at, id", (task_id,)
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            decoded = self._decode(row)
            if decoded is not None:
                result.append(decoded)
        return result

    def prepare(
        self,
        *,
        generation_run_id: str,
        task_id: str,
        task_stage: str,
        idempotency_key: str,
        request_hash: str,
        provider_id: str,
        model_id: str,
        prompt_key: str,
        prompt_version: str,
        prompt_hash: str,
        context_hash: str,
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM generation_attempts WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return self._decode(existing) or {}
            previous = conn.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) AS n FROM generation_attempts "
                "WHERE task_id=? AND task_stage=? AND request_hash=?",
                (task_id, task_stage, request_hash),
            ).fetchone()
            attempt_number = int(previous["n"] or 0) + 1
            attempt_id = hashlib.sha256(
                f"generation-attempt:{idempotency_key}".encode("utf-8")
            ).hexdigest()[:32]
            now = datetime.now().isoformat()
            conn.execute(
                """INSERT INTO generation_attempts(
                       id, generation_run_id, task_id, task_stage, attempt_number,
                       idempotency_key, request_hash, provider_id, model_id, prompt_key,
                       prompt_version, prompt_hash, context_hash, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)""",
                (
                    attempt_id, generation_run_id, task_id, task_stage, attempt_number,
                    idempotency_key, request_hash, provider_id, model_id, prompt_key,
                    prompt_version, prompt_hash, context_hash, now, now,
                ),
            )
            return self._decode(conn.execute(
                "SELECT * FROM generation_attempts WHERE id=?", (attempt_id,)
            ).fetchone()) or {}

    def mark_requesting(self, attempt_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE generation_attempts SET status='requesting', request_started_at=COALESCE(request_started_at, ?),
                   updated_at=? WHERE id=? AND status IN ('prepared', 'requesting')""",
                (datetime.now().isoformat(), datetime.now().isoformat(), attempt_id),
            )

    def persist_response(self, attempt_id: str, response: LLMResponse) -> dict[str, Any]:
        artifact = response_artifact(response)
        response_hash = stable_hash(artifact)
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE generation_attempts
                   SET status='persisted', provider_response_received_at=?, response_hash=?,
                       response_artifact=?, usage=?, latency_ms=?, updated_at=?
                   WHERE id=? AND status IN ('prepared', 'requesting', 'response_received', 'persisted')""",
                (
                    now, response_hash, _json(artifact), _json({
                        "tokensUsed": int(response.tokens_used or 0),
                        "promptTokens": int(response.prompt_tokens or 0),
                        "completionTokens": int(response.completion_tokens or 0),
                    }), int(response.latency_ms or 0), now, attempt_id,
                ),
            )
        return artifact

    def consume(self, attempt_id: str) -> None:
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE generation_attempts SET status='consumed', consumed_at=?, updated_at=?
                   WHERE id=? AND status IN ('response_received', 'persisted', 'consumed')""",
                (now, now, attempt_id),
            )

    def fail(self, attempt_id: str, code: str, detail: str = "") -> None:
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE generation_attempts SET status='failed', error_code=?, error_detail=?, updated_at=?
                   WHERE id=? AND status NOT IN ('persisted', 'consumed')""",
                (code, detail[:4000], now, attempt_id),
            )

    def abandon(self, attempt_id: str, reason: str = "retry_without_durable_response") -> None:
        now = datetime.now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE generation_attempts SET status='abandoned', error_code='ATTEMPT_ABANDONED',
                   error_detail=?, updated_at=? WHERE id=? AND status IN ('prepared', 'requesting')""",
                (reason[:4000], now, attempt_id),
            )
