#!/usr/bin/env python3
"""Generate a completion report from verification results, never agent claims."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter, defaultdict

from verify_features import ROOT, load_contracts


def verified(contract: dict) -> bool:
    command = [sys.executable, "-m", "pytest", *contract["acceptance_tests"]]
    return subprocess.run(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Run acceptance tests before reporting")
    args = parser.parse_args()
    contracts = load_contracts()
    states = defaultdict(Counter)
    for contract in contracts:
        state = "VERIFIED" if args.verify and verified(contract) else "UNVERIFIED"
        states[contract["priority"]][state] += 1
    total = len(contracts)
    verified_count = sum(group["VERIFIED"] for group in states.values())
    print("NovelForge Verification Report")
    print(f"Total features: {total}")
    for priority in sorted(states):
        group = states[priority]
        count = sum(group.values())
        print(f"{priority}: VERIFIED {group['VERIFIED']} / {count}; UNVERIFIED {group['UNVERIFIED']}")
    if args.verify:
        print(f"Verified completion: {verified_count} / {total} = {verified_count / total:.2%}")
    else:
        print("Verification not run; use --verify to calculate completion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

