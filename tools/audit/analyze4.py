#!/usr/bin/env python3
"""Phase 5 - bounded exact-duplicate scan (READ-ONLY).

Hashes only project-owned files (excludes .venv/.mimocode/.playwright-cli/
.references/.git) to find byte-identical duplicates. Groups by size first,
then hashes only sizes that appear more than once.
"""
import hashlib
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "tools", "audit", "_data")

with open(os.path.join(DATA, "files.jsonl"), encoding="utf-8") as f:
    files = [json.loads(line) for line in f if line.strip()]

EXCLUDE = {".venv", ".mimocode", ".playwright-cli", ".references", ".git",
           "__pycache__", ".pytest_cache", ".ruff_cache", "novelforge.egg-info"}

by_size = defaultdict(list)
for r in files:
    if r["topdir"] in EXCLUDE:
        continue
    if r["category"] == "git":
        continue
    if r["size"] >= 1024:
        by_size[r["size"]].append(r["path"])

def h(path):
    p = os.path.join(ROOT, path.replace("/", os.sep))
    hh = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hh.update(chunk)
    return hh.hexdigest()

groups = []
scanned = 0
for size, paths in by_size.items():
    if len(paths) < 2:
        continue
    hmap = defaultdict(list)
    for p in paths:
        try:
            hmap[h(p)].append(p)
        except OSError:
            continue
        scanned += 1
    for digest, ps in hmap.items():
        if len(ps) >= 2:
            groups.append((size, ps))

print(f"=== EXACT DUPLICATES (hashed {scanned} files) ===")
total_waste = 0
groups.sort(key=lambda g: -g[0] * (len(g[1]) - 1))
for size, ps in groups:
    waste = size * (len(ps) - 1)
    total_waste += waste
    print(f"  size={size/1e6:.2f}MB x{len(ps)}  waste={waste/1e6:.2f}MB")
    for p in ps[:5]:
        print(f"      {p}")
    if len(ps) > 5:
        print(f"      ... +{len(ps)-5} more")
print(f"\nTotal exact-duplicate groups: {len(groups)}")
print(f"Total reclaimable bytes (all-but-one copy): {total_waste/1e6:.2f} MB")
