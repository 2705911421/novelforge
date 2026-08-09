#!/usr/bin/env python3
"""Evaluate Feature Contracts from pytest exit codes; never edit contracts."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_contracts() -> list[dict]:
    contracts = []
    for path in sorted((ROOT / "spec" / "features").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        required = {"id", "name", "priority", "requirements", "acceptance_tests"}
        missing = required - data.keys()
        if missing:
            raise ValueError(f"{path.relative_to(ROOT)} missing: {', '.join(sorted(missing))}")
        data["_path"] = path
        contracts.append(data)
    return contracts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature", action="append", help="Feature ID to evaluate; repeatable")
    parser.add_argument("--list", action="store_true", help="List contracts without executing tests")
    args = parser.parse_args()

    contracts = load_contracts()
    if args.feature:
        requested = set(args.feature)
        contracts = [item for item in contracts if item["id"] in requested]
        unknown = requested - {item["id"] for item in contracts}
        if unknown:
            parser.error(f"unknown feature: {', '.join(sorted(unknown))}")

    if not contracts:
        print("No Feature Contracts found.")
        return 2

    exit_code = 0
    for item in contracts:
        command = [sys.executable, "-m", "pytest", *item["acceptance_tests"]]
        print(f"\n{item['id']} | {item['name']}")
        print("COMMAND:", " ".join(command))
        if args.list:
            continue
        result = subprocess.run(command, cwd=ROOT, check=False)
        state = "VERIFIED" if result.returncode == 0 else "UNVERIFIED"
        print(f"RESULT: {item['id']} {state} (exit {result.returncode})")
        if result.returncode != 0:
            exit_code = result.returncode
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
