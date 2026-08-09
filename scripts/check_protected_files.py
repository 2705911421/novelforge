#!/usr/bin/env python3
"""Reject changes to verification infrastructure unless explicitly bypassed."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path, PurePosixPath


PROTECTED = (
    "spec/features/",
    "tests/acceptance/",
    "scripts/verify_features.py",
    "scripts/generate_progress.py",
    "scripts/check_protected_files.py",
)
ROOT = Path(__file__).resolve().parents[1]


def changed_files(base: str | None) -> set[str]:
    commands = [["git", "diff", "--name-only"], ["git", "diff", "--cached", "--name-only"]]
    if base:
        commands.append(["git", "diff", "--name-only", f"{base}..HEAD"])
    files = set()
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            files.update(line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip())
    # A new protected artifact must not evade the local pre-commit check merely
    # because it has not been staged yet.
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if untracked.returncode == 0:
        files.update(line.strip().replace("\\", "/") for line in untracked.stdout.splitlines() if line.strip())
    return files


def is_protected(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix()
    return any(normalized == item or normalized.startswith(item) for item in PROTECTED)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="Merge-base SHA to inspect in CI")
    args = parser.parse_args()
    violations = sorted(path for path in changed_files(args.base) if is_protected(path))
    if violations:
        print("Protected verification artifacts changed:")
        print("\n".join(f"- {path}" for path in violations))
        print("Obtain explicit authorization before changing verification requirements.")
        return 1
    print("Protected verification artifacts unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
