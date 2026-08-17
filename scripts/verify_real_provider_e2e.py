"""Opt-in real-provider smoke test for GenerationAttempt recovery.

The default mode is intentionally non-networking. Pass
``--confirm-real-provider`` only when an operator wants to spend one provider
request against the configured writer route.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import Database  # noqa: E402
from src.core.generation_attempts import GenerationAttemptStore  # noqa: E402
from src.core.task_runtime import TaskRuntime  # noqa: E402
from src.llm.model_runtime import CredentialStore, ModelConfigurationError, ModelRepository, PersistentModelRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="projects/novelforge.db")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--confirm-real-provider", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--run-suffix", default="1")
    args = parser.parse_args()

    database = Database(args.db)
    repository = ModelRepository(database, CredentialStore(Path(args.workspace).resolve()))
    try:
        resolved = repository.resolve("writer")
        if not resolved.get("base_url") or not resolved.get("credential_ref"):
            raise ModelConfigurationError("MODEL_CONFIGURATION", "writer route has no base URL or credential")
        repository.credentials.resolve(resolved.get("credential_ref"))
    except Exception as exc:
        print(json.dumps({
            "status": "BLOCKED_REAL_PROVIDER",
            "reason": "writer route or credential is unavailable",
            "detail": str(exc),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.check_only or not args.confirm_real_provider:
        print(json.dumps({
            "status": "BLOCKED_REAL_PROVIDER",
            "reason": "real-provider execution is opt-in; pass --confirm-real-provider",
            "providerId": resolved.get("provider_id"),
            "modelId": resolved.get("model_id"),
        }, ensure_ascii=False, indent=2))
        return 0

    task = TaskRuntime(database).enqueue(
        "model-connection-test", data={"real_provider_e2e": True},
        idempotency_key=f"real-provider-e2e:generation-attempt-v2:{args.run_suffix}",
    )
    runtime = PersistentModelRuntime(repository)
    try:
        with runtime.task_scope(task["id"]):
            response = runtime.invoke(
                "writer",
                [{"role": "user", "content": "Reply with the single word READY."}],
                task_stage="real-provider-e2e",
                max_tokens=128,
            )
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED_REAL_PROVIDER", "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    attempts = GenerationAttemptStore(database).for_task(task["id"])
    passed = bool(response.content.strip()) and len(attempts) == 1 and attempts[0]["status"] == "consumed"
    print(json.dumps({
        "status": "IMPLEMENTED" if passed else "PARTIAL",
        "responseChars": len(response.content),
        "attemptCount": len(attempts),
        "attemptStatus": attempts[0]["status"] if attempts else None,
        "providerId": resolved.get("provider_id"),
        "modelId": resolved.get("model_id"),
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
