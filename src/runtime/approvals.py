"""Host-owned approval engine for effects that are not proposal-only.

When bound to a :class:`Database`, approval grants are stored in the durable
``runtime_approvals`` ledger and one-shot consumption is fenced in SQLite.
The memory-only mode remains useful for isolated unit tests and lightweight
embedders, but it must not be presented as restart-safe state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

from src.core.database import Database, generate_id

from .errors import DomainApprovalRequired


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


class ApprovalStatus(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"
    CONSUMED = "consumed"
    EXPIRED = "expired"


# Approval is a Host decision.  Keep the accepted actor vocabulary narrow so
# a provider/runtime label cannot be promoted to an authority credential by
# simply being copied into a task or control payload.
HOST_APPROVAL_ACTORS = frozenset({"author", "studio", "user", "human", "operator", "system"})
AUTHOR_APPROVAL_ACTORS = frozenset({"author", "studio", "user", "human", "operator"})
UNTRUSTED_APPROVAL_ACTORS = frozenset({
    "agent", "assistant", "provider", "runtime", "model", "codex", "claude", "gemini",
    "codex-app-server", "claude-code", "gemini-cli",
})


def _actor_key(actor: str | None) -> str:
    return str(actor or "").strip().lower()


def is_host_approval_actor(actor: str | None) -> bool:
    return _actor_key(actor) in HOST_APPROVAL_ACTORS


def is_author_approval_actor(actor: str | None) -> bool:
    return _actor_key(actor) in AUTHOR_APPROVAL_ACTORS


@dataclass(frozen=True)
class Approval:
    approval_id: str
    task_id: str
    tool_name: str
    domain: str
    status: ApprovalStatus = ApprovalStatus.REQUESTED
    requested_by: str = "system"
    approved_by: str | None = None
    reason: str = ""
    requested_at: str = ""
    decided_at: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "taskId": self.task_id,
            "toolName": self.tool_name,
            "domain": self.domain,
            "status": self.status.value,
            "requestedBy": self.requested_by,
            "approvedBy": self.approved_by,
            "reason": self.reason,
            "requestedAt": self.requested_at,
            "decidedAt": self.decided_at,
            "expiresAt": self.expires_at,
        }


class ApprovalEngine:
    """Issue and consume task/tool/domain-bound, explicit approval grants."""

    def __init__(self, *, default_ttl_seconds: int = 900, db: Database | None = None) -> None:
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be positive")
        self.default_ttl_seconds = int(default_ttl_seconds)
        self.db = db
        self._records: dict[str, Approval] = {}
        self._lock = threading.RLock()

    def request(
        self,
        task_id: str,
        tool_name: str,
        domain: str,
        *,
        requested_by: str = "system",
        ttl_seconds: int | None = None,
        reason: str = "",
    ) -> Approval:
        task_id = str(task_id).strip()
        tool_name = str(tool_name).strip()
        domain = str(domain).strip()
        if not task_id or not tool_name or not domain:
            raise ValueError("task_id, tool_name, and domain are required")
        ttl = self.default_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = _now()
        if self.db is None:
            with self._lock:
                for record in self._records.values():
                    if (
                        record.task_id == task_id
                        and record.tool_name == tool_name
                        and record.domain == domain
                        and record.status in {ApprovalStatus.REQUESTED, ApprovalStatus.APPROVED}
                    ):
                        if not self._expired(record):
                            return record
                        self._records[record.approval_id] = replace(
                            record,
                            status=ApprovalStatus.EXPIRED,
                            decided_at=_timestamp(now),
                        )
                record = Approval(
                    approval_id=f"approval-{generate_id()}",
                    task_id=task_id,
                    tool_name=tool_name,
                    domain=domain,
                    requested_by=str(requested_by or "system"),
                    reason=str(reason or ""),
                    requested_at=_timestamp(now),
                    expires_at=_timestamp(now + timedelta(seconds=ttl)),
                )
                self._records[record.approval_id] = record
                return record

        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT * FROM runtime_approvals
                   WHERE agent_task_id=? AND tool_name=? AND domain=?
                     AND status IN ('requested', 'approved')
                   ORDER BY requested_at DESC, id DESC LIMIT 1""",
                (task_id, tool_name, domain),
            ).fetchone()
            if row is not None:
                existing = self._from_row(row)
                if not self._expired(existing):
                    return existing
                conn.execute(
                    "UPDATE runtime_approvals SET status='expired', decided_at=? WHERE id=?",
                    (_timestamp(now), existing.approval_id),
                )
            approval_id = f"approval-{generate_id()}"
            conn.execute(
                """INSERT INTO runtime_approvals(
                       id, agent_task_id, tool_name, domain, status, requested_by,
                       approved_by, reason, requested_at, decided_at, expires_at
                   ) VALUES (?, ?, ?, ?, 'requested', ?, NULL, ?, ?, NULL, ?)""",
                (
                    approval_id,
                    task_id,
                    tool_name,
                    domain,
                    str(requested_by or "system"),
                    str(reason or ""),
                    _timestamp(now),
                    _timestamp(now + timedelta(seconds=ttl)),
                ),
            )
            row = conn.execute("SELECT * FROM runtime_approvals WHERE id=?", (approval_id,)).fetchone()
        return self._from_row(row)

    def approve(self, approval_id: str, *, approved_by: str = "system", reason: str = "") -> Approval:
        self._require_host_actor(approved_by)
        return self._decide(approval_id, ApprovalStatus.APPROVED, actor=approved_by, reason=reason)

    def reject(self, approval_id: str, *, rejected_by: str = "system", reason: str = "") -> Approval:
        self._require_host_actor(rejected_by)
        return self._decide(approval_id, ApprovalStatus.REJECTED, actor=rejected_by, reason=reason)

    def revoke(self, approval_id: str, *, revoked_by: str = "system", reason: str = "") -> Approval:
        self._require_host_actor(revoked_by)
        return self._decide(
            approval_id,
            ApprovalStatus.REVOKED,
            actor=revoked_by,
            reason=reason,
            allowed={ApprovalStatus.REQUESTED, ApprovalStatus.APPROVED},
        )

    @staticmethod
    def _require_host_actor(actor: str | None) -> str:
        """Keep every approval decision behind the Host actor boundary."""
        actor_key = _actor_key(actor)
        if actor_key in UNTRUSTED_APPROVAL_ACTORS or actor_key not in HOST_APPROVAL_ACTORS:
            raise DomainApprovalRequired(
                "provider/runtime actors cannot decide Host approvals",
                details={"actor": actor_key or None, "approvalCode": "HOST_ACTOR_REQUIRED"},
            )
        return actor_key

    def consume(
        self,
        task_id: str,
        tool_name: str,
        *,
        domain: str | None = None,
        approval_id: str | None = None,
    ) -> Approval:
        """Consume one approved grant bound to the exact task/tool/domain."""
        task_id = str(task_id).strip()
        tool_name = str(tool_name).strip()
        domain = str(domain).strip() if domain is not None else None
        if self.db is None:
            with self._lock:
                record = (
                    self._records.get(str(approval_id).strip())
                    if approval_id
                    else next(
                        (
                            candidate for candidate in self._records.values()
                            if candidate.task_id == task_id
                            and candidate.tool_name == tool_name
                            and (domain is None or candidate.domain == domain)
                        ),
                        None,
                    )
                )
                return self._consume_record(record, task_id, tool_name, domain)

        denial: tuple[str, dict[str, Any]] | None = None
        consumed: Approval | None = None
        now = _timestamp(_now())
        with self.db.transaction() as conn:
            if approval_id:
                row = conn.execute(
                    "SELECT * FROM runtime_approvals WHERE id=?",
                    (str(approval_id).strip(),),
                ).fetchone()
            else:
                domain_clause = " AND domain=?" if domain is not None else ""
                params: tuple[Any, ...] = (task_id, tool_name, *(([domain] if domain is not None else [])))
                row = conn.execute(
                    f"""SELECT * FROM runtime_approvals
                        WHERE agent_task_id=? AND tool_name=?{domain_clause}
                        ORDER BY requested_at DESC, id DESC LIMIT 1""",
                    params,
                ).fetchone()
            record = self._from_row(row) if row is not None else None
            if record is None:
                denial = (
                    f"no approval grant exists for {tool_name}",
                    {"taskId": task_id, "tool": tool_name},
                )
            elif (
                record.task_id != task_id
                or record.tool_name != tool_name
                or (domain is not None and record.domain != domain)
            ):
                denial = (
                    f"approval grant is not bound to {tool_name}",
                    {
                        "approvalId": record.approval_id,
                        "taskId": task_id,
                        "tool": tool_name,
                        "domain": domain,
                    },
                )
            elif record.status is not ApprovalStatus.APPROVED:
                denial = (
                    f"approval is not active for {tool_name}: {record.status.value}",
                    {"approvalId": record.approval_id, "status": record.status.value},
                )
            elif self._expired(record):
                conn.execute(
                    "UPDATE runtime_approvals SET status='expired', decided_at=? WHERE id=? AND status='approved'",
                    (now, record.approval_id),
                )
                denial = (
                    f"approval has expired for {tool_name}",
                    {"approvalId": record.approval_id},
                )
            else:
                changed = conn.execute(
                    """UPDATE runtime_approvals SET status='consumed', decided_at=?
                       WHERE id=? AND status='approved'""",
                    (now, record.approval_id),
                ).rowcount
                if changed != 1:
                    denial = (
                        f"approval is not active for {tool_name}",
                        {"approvalId": record.approval_id, "status": "consumed"},
                    )
                else:
                    consumed = replace(record, status=ApprovalStatus.CONSUMED, decided_at=now)
        if denial is not None:
            raise DomainApprovalRequired(denial[0], details=denial[1])
        assert consumed is not None
        return consumed

    def get(self, approval_id: str) -> Approval | None:
        key = str(approval_id).strip()
        if self.db is not None:
            row = self.db.fetchone("SELECT * FROM runtime_approvals WHERE id=?", (key,))
            return self._from_row(row) if row else None
        with self._lock:
            return self._records.get(key)

    def list(self, *, task_id: str | None = None) -> list[Approval]:
        if self.db is not None:
            if task_id is None:
                rows = self.db.fetchall("SELECT * FROM runtime_approvals ORDER BY requested_at, id")
            else:
                rows = self.db.fetchall(
                    "SELECT * FROM runtime_approvals WHERE agent_task_id=? ORDER BY requested_at, id",
                    (str(task_id),),
                )
            return [self._from_row(row) for row in rows]
        with self._lock:
            records = list(self._records.values())
        if task_id is not None:
            records = [record for record in records if record.task_id == str(task_id)]
        return sorted(records, key=lambda record: (record.requested_at, record.approval_id))

    def _decide(
        self,
        approval_id: str,
        status: ApprovalStatus,
        *,
        actor: str,
        reason: str,
        allowed: set[ApprovalStatus] | None = None,
    ) -> Approval:
        key = str(approval_id).strip()
        allowed_statuses = allowed or {ApprovalStatus.REQUESTED}
        if self.db is None:
            with self._lock:
                record = self._records.get(key)
                if record is None:
                    raise KeyError(f"approval not found: {key}")
                if record.status not in allowed_statuses:
                    raise DomainApprovalRequired(
                        f"approval cannot transition from {record.status.value}",
                        details={"approvalId": key, "status": record.status.value},
                    )
                if self._expired(record):
                    expired = replace(record, status=ApprovalStatus.EXPIRED, decided_at=_timestamp(_now()))
                    self._records[key] = expired
                    raise DomainApprovalRequired("approval has expired", details={"approvalId": key})
                updated = replace(
                    record,
                    status=status,
                    approved_by=str(actor or "system"),
                    reason=str(reason or record.reason),
                    decided_at=_timestamp(_now()),
                )
                self._records[key] = updated
                return updated

        failure: tuple[type[Exception], str, dict[str, Any]] | None = None
        updated: Approval | None = None
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM runtime_approvals WHERE id=?", (key,)).fetchone()
            record = self._from_row(row) if row is not None else None
            if record is None:
                failure = (KeyError, f"approval not found: {key}", {})
            elif record.status not in allowed_statuses:
                failure = (
                    DomainApprovalRequired,
                    f"approval cannot transition from {record.status.value}",
                    {"approvalId": key, "status": record.status.value},
                )
            elif self._expired(record):
                conn.execute(
                    "UPDATE runtime_approvals SET status='expired', decided_at=? WHERE id=?",
                    (_timestamp(_now()), key),
                )
                failure = (DomainApprovalRequired, "approval has expired", {"approvalId": key})
            else:
                decided_at = _timestamp(_now())
                changed = conn.execute(
                    """UPDATE runtime_approvals
                       SET status=?, approved_by=?, reason=?, decided_at=?
                       WHERE id=? AND status=?""",
                    (
                        status.value,
                        str(actor or "system"),
                        str(reason or record.reason),
                        decided_at,
                        key,
                        record.status.value,
                    ),
                ).rowcount
                if changed != 1:
                    failure = (
                        DomainApprovalRequired,
                        f"approval cannot transition from {record.status.value}",
                        {"approvalId": key, "status": record.status.value},
                    )
                else:
                    updated = replace(
                        record,
                        status=status,
                        approved_by=str(actor or "system"),
                        reason=str(reason or record.reason),
                        decided_at=decided_at,
                    )
        if failure is not None:
            exception_type, message, details = failure
            if exception_type is KeyError:
                raise KeyError(message)
            if issubclass(exception_type, DomainApprovalRequired):
                raise DomainApprovalRequired(message, details=details)
            raise exception_type(message)
        assert updated is not None
        return updated

    def _consume_record(
        self,
        record: Approval | None,
        task_id: str,
        tool_name: str,
        domain: str | None,
    ) -> Approval:
        if record is None:
            raise DomainApprovalRequired(
                f"no approval grant exists for {tool_name}",
                details={"taskId": task_id, "tool": tool_name},
            )
        if (
            record.task_id != task_id
            or record.tool_name != tool_name
            or (domain is not None and record.domain != domain)
        ):
            raise DomainApprovalRequired(
                f"approval grant is not bound to {tool_name}",
                details={
                    "approvalId": record.approval_id,
                    "taskId": task_id,
                    "tool": tool_name,
                    "domain": domain,
                },
            )
        if record.status is not ApprovalStatus.APPROVED:
            raise DomainApprovalRequired(
                f"approval is not active for {tool_name}: {record.status.value}",
                details={"approvalId": record.approval_id, "status": record.status.value},
            )
        if self._expired(record):
            expired = replace(record, status=ApprovalStatus.EXPIRED, decided_at=_timestamp(_now()))
            self._records[record.approval_id] = expired
            raise DomainApprovalRequired(
                f"approval has expired for {tool_name}",
                details={"approvalId": record.approval_id},
            )
        consumed = replace(record, status=ApprovalStatus.CONSUMED, decided_at=_timestamp(_now()))
        self._records[record.approval_id] = consumed
        return consumed

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> Approval:
        row = dict(row)
        return Approval(
            approval_id=str(row["id"]),
            task_id=str(row["agent_task_id"]),
            tool_name=str(row["tool_name"]),
            domain=str(row["domain"]),
            status=ApprovalStatus(str(row.get("status") or ApprovalStatus.REQUESTED.value)),
            requested_by=str(row.get("requested_by") or "system"),
            approved_by=str(row["approved_by"]) if row.get("approved_by") else None,
            reason=str(row.get("reason") or ""),
            requested_at=str(row.get("requested_at") or ""),
            decided_at=str(row["decided_at"]) if row.get("decided_at") else None,
            expires_at=str(row["expires_at"]) if row.get("expires_at") else None,
        )

    @staticmethod
    def _expired(record: Approval) -> bool:
        if not record.expires_at:
            return False
        try:
            return datetime.fromisoformat(record.expires_at) <= _now()
        except ValueError:
            return True


__all__ = [
    "Approval", "ApprovalEngine", "ApprovalStatus", "AUTHOR_APPROVAL_ACTORS",
    "HOST_APPROVAL_ACTORS", "UNTRUSTED_APPROVAL_ACTORS", "is_author_approval_actor",
    "is_host_approval_actor",
]
