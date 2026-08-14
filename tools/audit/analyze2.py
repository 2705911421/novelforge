#!/usr/bin/env python3
"""Phase 3 - precise repo composition + real maintained source (READ-ONLY)."""
import json
import os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "tools", "audit", "_data")

with open(os.path.join(DATA, "files.jsonl"), encoding="utf-8") as f:
    files = [json.loads(line) for line in f if line.strip()]

def mb(n):
    return n / 1e6

# ---- 1. tracked composition ----
print("=== GIT TRACKED: by topdir (size) ===")
tr = [r for r in files if r["git_state"] == "tracked"]
by_dir = defaultdict(lambda: [0, 0])
for r in tr:
    by_dir[r["topdir"]][0] += 1
    by_dir[r["topdir"]][1] += r["size"]
for d in sorted(by_dir, key=lambda x: -by_dir[x][1]):
    print(f"  {d:45s} files={by_dir[d][0]:>6} size={mb(by_dir[d][1]):>8.1f}MB")

print()
print("=== GIT TRACKED: by extension (top 25 by size) ===")
by_ext = defaultdict(lambda: [0, 0])
for r in tr:
    by_ext[r["ext"]][0] += 1
    by_ext[r["ext"]][1] += r["size"]
for e in sorted(by_ext, key=lambda x: -by_ext[x][1])[:25]:
    print(f"  .{e or '(none)':22s} files={by_ext[e][0]:>6} size={mb(by_ext[e][1]):>8.1f}MB")

print()
print("=== UNTRACKED (non-ignored) files ===")
for r in sorted([r for r in files if r["git_state"] == "untracked"], key=lambda x: x["path"]):
    print(f"  {r['path']}  ({r['size']} bytes)")

# ---- 2. maintained dirs extension/gitstate/LOC ----
print()
print("=== MAINTAINED DIRS: ext x gitstate x LOC ===")
MAINTAINED = {"src", "tests", "scripts", "spec", "config", "studio"}
for d in MAINTAINED:
    dd = [r for r in files if r["topdir"] == d]
    if not dd:
        continue
    ext = Counter(r["ext"] for r in dd)
    states = Counter(r["git_state"] for r in dd)
    sz = sum(r["size"] for r in dd)
    loc = 0
    locfiles = 0
    for r in dd:
        if r["size"] > 2_000_000:
            continue
        try:
            with open(os.path.join(ROOT, r["path"].replace("/", os.sep)), "r",
                      encoding="utf-8", errors="ignore") as fh:
                loc += sum(1 for _ in fh)
            locfiles += 1
        except OSError:
            pass
    ext_s = ", ".join(f".{e or '(none)'}={n}" for e, n in ext.most_common(8))
    print(f"  {d:10s} files={len(dd):>4} size={mb(sz):>6.1f}MB states={dict(states)}")
    print(f"           ext: {ext_s}")
    print(f"           LOC={loc} ({locfiles} text files)")

# ---- 3. category attribution: which topdirs make each category ----
print()
print("=== CATEGORY x TOPDIR (top contributors per category) ===")
cat_dir = defaultdict(lambda: defaultdict(lambda: [0, 0]))
for r in files:
    c = r["category"]
    cat_dir[c][r["topdir"]][0] += 1
    cat_dir[c][r["topdir"]][1] += r["size"]
for c in sorted(cat_dir, key=lambda x: -sum(v[1] for v in cat_dir[x].values())):
    tot = sum(v[0] for v in cat_dir[c].values())
    totsz = sum(v[1] for v in cat_dir[c].values())
    tops = sorted(cat_dir[c].items(), key=lambda x: -x[1][1])[:5]
    ts = ", ".join(f"{t[0]}:{t[1][0]}f/{mb(t[1][1]):.1f}MB" for t in tops)
    print(f"  {c:16s} total={tot:>6}f/{mb(totsz):>7.1f}MB  -> {ts}")

# ---- 4. .storyflow aggregate + .phase5 aggregate ----
print()
print("=== AGENT-GENERATED DIR GROUPS ===")
groups = {
    ".storyflow-*": lambda r: r["topdir"].startswith(".storyflow-"),
    ".phase5-*": lambda r: r["topdir"].startswith(".phase5-"),
}
for name, pred in groups.items():
    gg = [r for r in files if pred(r)]
    print(f"  {name:16s} dirs={len(set(r['topdir'] for r in gg)):>3} files={len(gg):>5} size={mb(sum(r['size'] for r in gg)):>7.1f}MB")
