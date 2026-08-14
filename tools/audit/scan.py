#!/usr/bin/env python3
"""Repository Health Audit - Phase 1 inventory scanner (READ-ONLY).

Walks the repository, records every file with size/extension/category,
computes per-directory aggregates, and cross-references git
tracked/untracked/ignored sets. Writes raw data under _data/ and prints
a summary. This script never deletes or modifies project files.
"""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "tools", "audit", "_data")
os.makedirs(DATA, exist_ok=True)

# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------

# Extension -> category (lowercased ext without dot)
EXT_CATEGORY = {
    # Source code
    "py": "source", "ts": "source", "tsx": "source", "js": "source",
    "jsx": "source", "mjs": "source", "cjs": "source", "rs": "source",
    "go": "source", "java": "source", "kt": "source", "kts": "source",
    "cpp": "source", "cc": "source", "cxx": "source", "c": "source",
    "h": "source", "hpp": "source", "hxx": "source", "cs": "source",
    "swift": "source", "php": "source", "rb": "source", "scala": "source",
    "sh": "source", "bash": "source", "ps1": "source", "bat": "source",
    "cmd": "source", "css": "source", "scss": "source", "sass": "source",
    "less": "source", "html": "source", "htm": "source", "vue": "source",
    "svelte": "source", "sql": "source", "graphql": "source", "gql": "source",
    "proto": "source", "prisma": "source", "dockerfile": "source",
    "makefile": "source", "mk": "source", "toml": "config", "yaml": "config",
    "yml": "config", "ini": "config", "cfg": "config", "conf": "config",
    "json": "data", "json5": "data", "jsonl": "data", "ndjson": "data",
    "csv": "data", "tsv": "data", "parquet": "data", "pickle": "data",
    "pkl": "data", "db": "data", "sqlite": "data", "sqlite3": "data",
    "dump": "data", "lock": "config", "txt": "text", "md": "docs",
    "mdx": "docs", "rst": "docs", "adoc": "docs", "tex": "docs",
    "log": "logs", "out": "logs", "tmp": "temp", "temp": "temp",
    "bak": "temp", "backup": "temp", "old": "temp", "orig": "temp",
    "pyc": "cache", "pyo": "cache", "tsbuildinfo": "cache", "cache": "cache",
    "map": "build", "min.js": "build", "min.css": "build",
    "png": "assets", "jpg": "assets", "jpeg": "assets", "gif": "assets",
    "webp": "assets", "svg": "assets", "ico": "assets", "bmp": "assets",
    "avif": "assets", "mp4": "assets", "webm": "assets", "mov": "assets",
    "mp3": "assets", "wav": "assets", "ogg": "assets", "flac": "assets",
    "ttf": "assets", "otf": "assets", "woff": "assets", "woff2": "assets",
    "eot": "assets", "pdf": "assets", "docx": "assets", "xlsx": "assets",
    "pptx": "assets", "zip": "assets", "tar": "assets", "gz": "assets",
    "tgz": "assets", "7z": "assets", "rar": "assets", "whl": "dependencies",
    "jar": "dependencies", "so": "dependencies", "dll": "dependencies",
    "dylib": "dependencies", "a": "dependencies", "lib": "dependencies",
    "egg": "dependencies", "exe": "build", "bin": "build",
    "wasm": "build", "o": "build", "obj": "build", "class": "build",
    "lockb": "cache", "pem": "config", "key": "config",
    "crt": "config", "env": "config", "editorconfig": "config",
    "gitignore": "config", "gitattributes": "config", "ignore": "config",
    "feature": "config", "ipynb": "docs",
}

# Directory-name fragments -> category (checked on relative path components)
DIR_CATEGORY_HINTS = [
    ("__pycache__", "cache"),
    (".pytest_cache", "cache"),
    (".ruff_cache", "cache"),
    (".mypy_cache", "cache"),
    (".cache", "cache"),
    (".next", "cache"),
    (".turbo", "cache"),
    (".parcel-cache", "cache"),
    (".vite", "cache"),
    (".eslintcache", "cache"),
    (".tox", "cache"),
    ("node_modules", "dependencies"),
    ("site-packages", "dependencies"),
    (".venv", "dependencies"),
    ("venv", "dependencies"),
    ("vendor", "dependencies"),
    (".terraform", "dependencies"),
    ("dist", "build"),
    ("build", "build"),
    ("target", "build"),
    ("release", "build"),
    ("out", "build"),
    ("egg-info", "build"),
    (".git", "git"),
    ("test-results", "test_artifacts"),
    ("test-output", "test_artifacts"),
    ("playwright-report", "test_artifacts"),
    ("playwright-report", "test_artifacts"),
    ("screenshots", "test_artifacts"),
    ("coverage", "test_artifacts"),
    ("__snapshots__", "test_artifacts"),
    ("reports", "reports"),
    ("logs", "logs"),
    ("screenshots", "test_artifacts"),
]


def dir_hint_category(rel_dir):
    parts = [p.lower() for p in rel_dir.replace("\\", "/").split("/")]
    for hint, cat in DIR_CATEGORY_HINTS:
        if hint in parts:
            return cat
    return None


def classify(path, rel_dir, ext, is_dir):
    # .git handled at walk level; classify by dir hints first
    c = dir_hint_category(rel_dir)
    if c:
        return c
    if ext in EXT_CATEGORY:
        return EXT_CATEGORY[ext]
    return "other"


def is_source_ext(ext):
    return EXT_CATEGORY.get(ext) == "source"


def main():
    # ---------------- disk walk ----------------
    git_dir = os.path.join(ROOT, ".git")
    files = []  # list of dicts for every file under ROOT except .git
    git_files = []
    dir_stats = {}  # rel_dir -> {files, dirs, size}

    for dirpath, dirnames, filenames in os.walk(ROOT):
        # skip .git subtree but record it separately
        rel = os.path.relpath(dirpath, ROOT)
        rel = "" if rel == "." else rel
        if rel.startswith(".git") and rel not in (".git",):
            continue
        if rel == ".git":
            for d, ds, fs in os.walk(git_dir):
                for fn in fs:
                    fp = os.path.join(d, fn)
                    try:
                        git_files.append((fp, os.path.getsize(fp)))
                    except OSError:
                        pass
            dirnames[:] = []
            continue

        norm = rel.replace("\\", "/")
        ds = dir_stats.setdefault(norm, {"files": 0, "dirs": len(dirnames), "size": 0})
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = 0
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            rec = {
                "path": (norm + "/" + fn) if norm else fn,
                "size": size,
                "ext": ext,
                "topdir": (norm.split("/")[0] if norm else "(root)"),
                "category": classify(fn, norm, ext, False),
                "tracked": None,  # filled below
                "git_state": None,
            }
            files.append(rec)
            ds["files"] += 1
            ds["size"] += size

    # ---------------- git cross-reference ----------------
    def git_paths(args):
        out = subprocess.run(
            ["git", "-c", "core.quotepath=false"] + args,
            cwd=ROOT, capture_output=True,
        )
        raw = out.stdout
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", "replace")
        return set(text.split("\0"))

    tracked = git_paths(["ls-files", "-z"])
    untracked = git_paths(["ls-files", "--others", "--exclude-standard", "-z"])
    ignored = git_paths(["ls-files", "--others", "--ignored", "--exclude-standard", "-z"])

    by_path = {}
    for r in files:
        by_path.setdefault(r["path"], []).append(r)

    for r in files:
        p = r["path"].replace("\\", "/")
        if p in tracked:
            r["git_state"] = "tracked"
        elif p in untracked:
            r["git_state"] = "untracked"
        elif p in ignored:
            r["git_state"] = "ignored"
        else:
            r["git_state"] = "unknown"

    # ---------------- write data ----------------
    with open(os.path.join(DATA, "files.jsonl"), "w", encoding="utf-8") as f:
        for r in files:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(os.path.join(DATA, "dir_stats.json"), "w", encoding="utf-8") as f:
        json.dump(dir_stats, f, ensure_ascii=False, indent=1)

    with open(os.path.join(DATA, "git_sets.json"), "w", encoding="utf-8") as f:
        json.dump({
            "tracked": sorted(tracked),
            "untracked": sorted(untracked),
            "ignored": sorted(ignored),
        }, f, ensure_ascii=False, indent=1)

    # ---------------- summary ----------------
    total_files = len(files)
    total_size = sum(r["size"] for r in files)
    git_file_count = len(git_files)
    git_file_size = sum(s for _, s in git_files)

    state_counts = {}
    state_sizes = {}
    for r in files:
        s = r["git_state"]
        state_counts[s] = state_counts.get(s, 0) + 1
        state_sizes[s] = state_sizes.get(s, 0) + r["size"]

    cat_counts = {}
    cat_sizes = {}
    for r in files:
        c = r["category"]
        cat_counts[c] = cat_counts.get(c, 0) + 1
        cat_sizes[c] = cat_sizes.get(c, 0) + r["size"]

    print("=== SCAN SUMMARY ===")
    print(f"ROOT: {ROOT}")
    print(f"Total files (excl .git): {total_files}")
    print(f"Total size  (excl .git): {total_size:,} bytes ({total_size/1024/1024:.1f} MB)")
    print(f".git files: {git_file_count}, .git size: {git_file_size:,} bytes ({git_file_size/1024/1024:.1f} MB)")
    print()
    print("=== GIT STATE (on-disk files excl .git) ===")
    for s in ("tracked", "untracked", "ignored", "unknown"):
        print(f"  {s:10s} files={state_counts.get(s,0):>7}  size={state_sizes.get(s,0):>14,} ({state_sizes.get(s,0)/1024/1024:.1f} MB)")
    print(f"  git ls-files tracked raw={len(tracked)}, untracked raw={len(untracked)}, ignored raw={len(ignored)}")
    print()
    print("=== CATEGORY (heuristic) ===")
    for c in sorted(cat_counts, key=lambda x: -cat_sizes[x]):
        print(f"  {c:16s} files={cat_counts[c]:>7}  size={cat_sizes[c]:>14,} ({cat_sizes[c]/1024/1024:.1f} MB)")

    print()
    print("=== TOP-LEVEL DIRS (by size) ===")
    tops = {}
    for r in files:
        t = r["topdir"]
        tops.setdefault(t, [0, 0])
        tops[t][0] += 1
        tops[t][1] += r["size"]
    for t in sorted(tops, key=lambda x: -tops[x][1]):
        print(f"  {t:55s} files={tops[t][0]:>7}  size={tops[t][1]:>14,} ({tops[t][1]/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
