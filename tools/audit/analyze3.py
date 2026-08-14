#!/usr/bin/env python3
"""Phase 4 - clean maintained-source LOC and size (READ-ONLY).

Counts ONLY tracked, text source/config/doc files, excluding all
dependency/build/cache/vendor/reference/agent-state trees.
"""
import json
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "tools", "audit", "_data")

with open(os.path.join(DATA, "files.jsonl"), encoding="utf-8") as f:
    files = [json.loads(line) for line in f if line.strip()]

EXCLUDE_TOPDIRS = {
    ".venv", ".references", ".mimocode", ".playwright-cli", "projects",
    "output", "exports", "dist", "test-output", ".agents", ".reasonix",
    ".claude", "__pycache__", ".pytest_cache", ".ruff_cache",
    "novelforge.egg-info", ".novelforge-secrets", ".novelforge-backups",
    ".github",
}
def excluded(r):
    td = r["topdir"]
    if td in EXCLUDE_TOPDIRS:
        return True
    if td.startswith(".phase5-") or td.startswith(".storyflow-"):
        return True
    return False

SOURCE_EXT = {"py", "js", "jsx", "ts", "tsx", "css", "scss", "html", "htm",
              "sql", "sh", "ps1", "bat", "cmd", "mjs", "cjs"}
CONFIG_EXT = {"yaml", "yml", "toml", "ini", "cfg", "json", "lock"}
DOC_EXT = {"md", "rst", "txt", "adoc"}

def bucket(r):
    if r["ext"] in SOURCE_EXT:
        return "source"
    if r["ext"] in CONFIG_EXT:
        return "config"
    if r["ext"] in DOC_EXT:
        return "docs"
    return None

# Only tracked + text, exclude binary-ish even if ext matches
rows = []
for r in files:
    if r["git_state"] != "tracked":
        continue
    if excluded(r):
        continue
    b = bucket(r)
    if b is None:
        continue
    rows.append((r, b))

loc = Counter()
fcount = Counter()
fsize = Counter()
by_dir = {}
for r, b in rows:
    p = os.path.join(ROOT, r["path"].replace("/", os.sep))
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
            n = sum(1 for _ in fh)
    except OSError:
        continue
    loc[b] += n
    fcount[b] += 1
    fsize[b] += r["size"]
    td = r["topdir"]
    d = by_dir.setdefault(td, Counter())
    d[b] += n

print("=== MAINTAINED (tracked, text, excluding vendor/deps/reference) ===")
for b in ("source", "docs", "config"):
    print(f"  {b:8s} files={fcount[b]:>5} LOC={loc[b]:>8} size={fsize[b]/1e3:>8.1f}KB")
tot = sum(loc.values())
print(f"  {'TOTAL':8s} files={sum(fcount.values()):>5} LOC={tot:>8} size={sum(fsize.values())/1e3:>8.1f}KB")
print()
print("=== LOC by topdir (maintained only) ===")
for td in sorted(by_dir, key=lambda x: -sum(by_dir[x].values())):
    d = by_dir[td]
    parts = ", ".join(f"{k}={v}" for k, v in d.most_common())
    print(f"  {td:12s} LOC={sum(d.values()):>7}  [{parts}]")

# test files specifically (tracked)
print()
print("=== TRACKED TEST FILES ===")
test_rows = [r for r in files if r["git_state"] == "tracked"
             and (r["topdir"] == "tests" or "/tests/" in r["path"])]
tloc = 0
tfiles = 0
for r in test_rows:
    p = os.path.join(ROOT, r["path"].replace("/", os.sep))
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
            tloc += sum(1 for _ in fh)
        tfiles += 1
    except OSError:
        pass
print(f"  tracked test files={len(test_rows)} (text-readable LOC={tloc} over {tfiles} files)")

# source files in src/ specifically
print()
print("=== TRACKED SRC FILES ===")
src_rows = [r for r in files if r["git_state"] == "tracked" and r["topdir"] == "src"]
sloc = 0
sfiles = 0
for r in src_rows:
    p = os.path.join(ROOT, r["path"].replace("/", os.sep))
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
            sloc += sum(1 for _ in fh)
        sfiles += 1
    except OSError:
        pass
print(f"  tracked src files={len(src_rows)} (text-readable LOC={sloc} over {sfiles} files)")
