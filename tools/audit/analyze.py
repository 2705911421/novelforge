#!/usr/bin/env python3
"""Repository Health Audit - Phase 2 analyzer (READ-ONLY).

Reads _data/files.jsonl produced by scan.py and emits:
  - per-top-level-dir breakdown incl. git state
  - per-category breakdown
  - extension histogram
  - level-2 hotspot directories
  - LOC per category (source/tests/docs/config), excluding deps/build/cache
"""
import json
import os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "tools", "audit", "_data")

with open(os.path.join(DATA, "files.jsonl"), encoding="utf-8") as f:
    files = [json.loads(line) for line in f if line.strip()]

SOURCE_EXTS = {"py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "rs", "go", "java",
               "kt", "kts", "cpp", "cc", "cxx", "c", "h", "hpp", "cs", "swift",
               "php", "rb", "scala", "sh", "bash", "ps1", "bat", "cmd", "css",
               "scss", "sass", "less", "html", "htm", "vue", "svelte", "sql",
               "graphql", "gql", "proto", "prisma", "mk"}
TEST_NAMES = ("test_", "_test.", ".test.", ".spec.", "__tests__")

def is_test(rec):
    p = rec["path"].lower()
    if "/tests/" in p or p.startswith("tests/") or "test-output" in p:
        return True
    base = os.path.basename(rec["path"]).lower()
    return any(t in base for t in TEST_NAMES)

# ---- top-level dir breakdown with git state ----
print("=== TOP-LEVEL DIR x GIT STATE ===")
topdirs = {}
for r in files:
    t = r["topdir"]
    d = topdirs.setdefault(t, {"files": 0, "size": 0, "states": Counter()})
    d["files"] += 1
    d["size"] += r["size"]
    d["states"][r["git_state"]] += 1

for t in sorted(topdirs, key=lambda x: -topdirs[x]["size"]):
    d = topdirs[t]
    st = ", ".join(f"{k}={v}" for k, v in d["states"].most_common())
    print(f"  {t:52s} files={d['files']:>6} size={d['size']/1e6:>8.1f}MB  [{st}]")

# ---- category breakdown ----
print()
print("=== CATEGORY BREAKDOWN ===")
cats = {}
for r in files:
    c = r["category"]
    d = cats.setdefault(c, {"files": 0, "size": 0, "states": Counter()})
    d["files"] += 1
    d["size"] += r["size"]
    d["states"][r["git_state"]] += 1
for c in sorted(cats, key=lambda x: -cats[x]["size"]):
    d = cats[c]
    st = ", ".join(f"{k}={v}" for k, v in d["states"].most_common())
    print(f"  {c:16s} files={d['files']:>6} size={d['size']/1e6:>8.1f}MB  [{st}]")

# ---- extension histogram ----
print()
print("=== EXTENSION HISTOGRAM (top 40 by count) ===")
ext = Counter(r["ext"] for r in files)
ext_size = defaultdict(int)
for r in files:
    ext_size[r["ext"]] += r["size"]
for e, n in ext.most_common(40):
    print(f"  .{e or '(none)':24s} files={n:>6} size={ext_size[e]/1e6:>8.1f}MB")

# ---- level-2 hotspot dirs ----
print()
print("=== LEVEL-2 DIRS (top 30 by size) ===")
lvl2 = {}
for r in files:
    parts = r["path"].split("/")
    if len(parts) >= 2:
        key = parts[0] + "/" + parts[1]
    elif len(parts) == 1:
        key = "(root)/" + parts[0]
    else:
        key = r["path"]
    d = lvl2.setdefault(key, {"files": 0, "size": 0})
    d["files"] += 1
    d["size"] += r["size"]
for k in sorted(lvl2, key=lambda x: -lvl2[x]["size"])[:30]:
    d = lvl2[k]
    print(f"  {k:60s} files={d['files']:>6} size={d['size']/1e6:>8.1f}MB")

# ---- LOC counting (source/tests/docs/config only) ----
print()
print("=== LOC (excluding deps/build/cache; categories source/test/docs/config) ===")
loc_stats = Counter()
loc_files = Counter()
SKIP = {"dependencies", "build", "cache", "git", "assets", "data", "logs",
        "test_artifacts", "temp", "other", "text"}
for r in files:
    if r["category"] in SKIP:
        continue
    if r["category"] == "source":
        pass
    elif r["category"] in ("docs", "config"):
        pass
    else:
        continue
    # only read text-like files
    if r["size"] > 2_000_000:
        continue
    try:
        with open(os.path.join(ROOT, r["path"].replace("/", os.sep)), "r",
                  encoding="utf-8", errors="ignore") as fh:
            n = sum(1 for _ in fh)
    except OSError:
        continue
    if is_test(r):
        loc_stats["tests"] += n
        loc_files["tests"] += 1
    else:
        loc_stats[r["category"]] += n
        loc_files[r["category"]] += 1
for c in ("source", "tests", "docs", "config"):
    print(f"  {c:10s} files={loc_files[c]:>6} LOC={loc_stats[c]:>8}")

total_loc = sum(loc_stats.values())
total_loc_files = sum(loc_files.values())
print(f"  {'TOTAL':10s} files={total_loc_files:>6} LOC={total_loc:>8}")
