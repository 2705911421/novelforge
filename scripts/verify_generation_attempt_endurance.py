"""Deterministic endurance check for GenerationAttempt persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import Database  # noqa: E402
from src.core.generation_attempts import GenerationAttemptStore  # noqa: E402
from src.core.task_runtime import TaskRuntime  # noqa: E402
from src.llm.gateway import LLMResponse  # noqa: E402
from src.llm.model_runtime import CredentialStore, ModelRepository, PersistentModelRuntime  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="novelforge-attempt-endurance-") as directory:
        root = Path(directory)
        os.environ["ENDURANCE_KEY"] = "deterministic-test-key"
        db = Database(str(root / "endurance.db"))
        repository = ModelRepository(db, CredentialStore(root))
        repository.save_configuration({
            "providers": [{"id": "p", "name": "endurance", "providerType": "custom", "baseUrl": "https://endurance.invalid/v1", "credentialEnv": "ENDURANCE_KEY"}],
            "models": [{"id": "m", "providerId": "p", "name": "endurance", "modelId": "endurance-v1"}],
            "routes": {"writer": "m"},
        })
        task = TaskRuntime(db).enqueue("write-next", idempotency_key="attempt-endurance-task")

        class Gateway:
            calls = 0

            def register_provider(self, _name, _config):
                return None

            def chat(self, _name, _messages, _system, **_kwargs):
                self.calls += 1
                return LLMResponse(content=f"ok-{self.calls}", model="endurance-v1", tokens_used=1)

        gateway = Gateway()
        runtime = PersistentModelRuntime(repository, gateway=gateway)
        with runtime.task_scope(task["id"]):
            for index in range(100):
                runtime.invoke(
                    "writer", [{"role": "user", "content": f"endurance-{index}"}],
                    task_stage=f"endurance-{index}", max_tokens=8,
                )
        attempts = GenerationAttemptStore(db).for_task(task["id"])
        passed = len(attempts) == 100 and gateway.calls == 100 and all(item["status"] == "consumed" for item in attempts)
        print(json.dumps({
            "status": "IMPLEMENTED" if passed else "PARTIAL",
            "attemptCount": len(attempts),
            "providerCalls": gateway.calls,
            "allConsumed": all(item["status"] == "consumed" for item in attempts),
            "restartSchemaVersion": db.fetchone("SELECT MAX(version) AS version FROM schema_migrations")["version"],
        }, ensure_ascii=False, indent=2))
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
