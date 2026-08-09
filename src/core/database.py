"""
NovelForge 数据库管理模块
提供数据库初始化、迁移和基础 CRUD 操作
"""

import sqlite3
import uuid
import hashlib
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# 数据库版本
# ``db_version`` is retained strictly for reading databases created by the
# pre-migration application.  New schema evolution is recorded in
# ``schema_migrations`` below and must never mutate an applied migration.
DB_VERSION = 1

# 数据库 Schema
SCHEMA_SQL = """
-- 版本表
CREATE TABLE IF NOT EXISTS db_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 项目表
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    genre TEXT,
    description TEXT,
    target_chapters INTEGER DEFAULT 100,
    chapter_words_min INTEGER DEFAULT 2000,
    chapter_words_max INTEGER DEFAULT 4000,
    language TEXT DEFAULT 'zh-CN',
    writing_style TEXT,
    author_intent TEXT,
    world_setting TEXT, -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 书籍表
CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    genre TEXT,
    status TEXT DEFAULT 'active',
    total_words INTEGER DEFAULT 0,
    total_chapters INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 卷表
CREATE TABLE IF NOT EXISTS volumes (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT,
    description TEXT,
    target_chapters INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, number)
);

-- 故事弧表
CREATE TABLE IF NOT EXISTS arcs (
    id TEXT PRIMARY KEY,
    volume_id TEXT NOT NULL REFERENCES volumes(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT,
    description TEXT,
    theme TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(volume_id, number)
);

-- 章节表
CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    arc_id TEXT REFERENCES arcs(id),
    number INTEGER NOT NULL,
    title TEXT,
    content TEXT,
    summary TEXT,
    word_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft',
    key_events TEXT, -- JSON array
    characters_appeared TEXT, -- JSON array
    locations_used TEXT, -- JSON array
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, number)
);

-- 章节版本表
CREATE TABLE IF NOT EXISTS chapter_versions (
    id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    word_count INTEGER,
    change_summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chapter_id, version)
);

-- 角色表
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    personality TEXT,
    background TEXT,
    goals TEXT,
    flaws TEXT,
    appearance TEXT,
    importance TEXT DEFAULT 'minor',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 角色状态表
CREATE TABLE IF NOT EXISTS character_states (
    id TEXT PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    location TEXT,
    status TEXT,
    relationships TEXT, -- JSON
    knowledge TEXT, -- JSON array
    emotional_state TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 势力表
CREATE TABLE IF NOT EXISTS factions (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    goals TEXT,
    resources TEXT,
    leadership TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 势力状态表
CREATE TABLE IF NOT EXISTS faction_states (
    id TEXT PRIMARY KEY,
    faction_id TEXT NOT NULL REFERENCES factions(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    territory TEXT, -- JSON
    power_level TEXT,
    allies TEXT, -- JSON array
    enemies TEXT, -- JSON array
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 地点表
CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES locations(id),
    name TEXT NOT NULL,
    description TEXT,
    type TEXT, -- world/continent/country/city/building
    significance TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 地点状态表
CREATE TABLE IF NOT EXISTS location_states (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    controlling_faction TEXT,
    events TEXT, -- JSON array
    condition TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 世界规则表
CREATE TABLE IF NOT EXISTS world_rules (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    category TEXT,
    rule_text TEXT NOT NULL,
    examples TEXT,
    exceptions TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 力量体系表
CREATE TABLE IF NOT EXISTS power_systems (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    levels TEXT, -- JSON array
    rules TEXT,
    limitations TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 关系表
CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL, -- character/faction
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relationship_type TEXT,
    description TEXT,
    strength INTEGER DEFAULT 5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 时间线事件表
CREATE TABLE IF NOT EXISTS timeline_events (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_id TEXT REFERENCES chapters(id),
    event_time TEXT, -- story time
    event_type TEXT,
    title TEXT,
    description TEXT,
    characters_involved TEXT, -- JSON array
    location TEXT,
    significance TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 伏笔表
CREATE TABLE IF NOT EXISTS foreshadows (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    created_chapter INTEGER NOT NULL,
    resolved_chapter INTEGER,
    title TEXT,
    description TEXT,
    status TEXT DEFAULT 'open', -- open/advanced/resolved
    priority TEXT DEFAULT 'medium',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 钩子表
CREATE TABLE IF NOT EXISTS hooks (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_id TEXT REFERENCES chapters(id),
    hook_type TEXT,
    description TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Story Fact 表
CREATE TABLE IF NOT EXISTS story_facts (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    fact_type TEXT NOT NULL,
    content TEXT NOT NULL,
    entities TEXT, -- JSON array
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Story Commit 表
CREATE TABLE IF NOT EXISTS story_commits (
    id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'pending', -- pending/accepted/rejected
    facts_extracted TEXT, -- JSON
    state_changes TEXT, -- JSON
    review_score REAL,
    blocking_issues INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 审查表
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    review_type TEXT DEFAULT 'chapter', -- chapter/joint
    overall_score REAL,
    passed BOOLEAN DEFAULT FALSE,
    verdict TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 审查维度表
CREATE TABLE IF NOT EXISTS review_dimensions (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    score REAL,
    weight REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 审查问题表
CREATE TABLE IF NOT EXISTS review_issues (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    dimension TEXT,
    severity TEXT DEFAULT 'medium', -- low/medium/high/critical
    blocking BOOLEAN DEFAULT FALSE,
    location TEXT,
    description TEXT NOT NULL,
    reason TEXT,
    suggestion TEXT,
    status TEXT DEFAULT 'open', -- open/fixed/ignored
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 修订表
CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    review_id TEXT REFERENCES reviews(id),
    revision_type TEXT DEFAULT 'full', -- local/scene/full
    issues_fixed TEXT, -- JSON array
    before_content TEXT,
    after_content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 参考文档表
CREATE TABLE IF NOT EXISTS reference_documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    doc_type TEXT, -- world/character/style/reference
    file_path TEXT,
    content TEXT,
    word_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 文档分块表
CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES reference_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding BLOB, -- vector embedding
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Prompt 表
CREATE TABLE IF NOT EXISTS prompts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category TEXT,
    content TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    is_default BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Skill 表
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    instructions TEXT,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 任务表
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL, -- write/continuous/review/export
    status TEXT DEFAULT 'pending', -- pending/running/completed/failed/cancelled
    book_id TEXT,
    chapter_number INTEGER,
    data JSON,
    result JSON,
    error TEXT,
    progress INTEGER DEFAULT 0,
    total_steps INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 任务检查点表
CREATE TABLE IF NOT EXISTS task_checkpoints (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stage TEXT,
    state JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 备份表
CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    backup_type TEXT DEFAULT 'manual', -- manual/auto/chapter
    file_path TEXT NOT NULL,
    size_bytes INTEGER,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 操作日志表
CREATE TABLE IF NOT EXISTS operation_logs (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    details JSON,
    duration_ms INTEGER,
    token_count INTEGER,
    model_used TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 模型配置表
CREATE TABLE IF NOT EXISTS model_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider_type TEXT, -- openai/anthropic/gemini/custom
    base_url TEXT,
    api_key TEXT,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY,
    provider_id TEXT REFERENCES model_providers(id),
    name TEXT NOT NULL,
    model_id TEXT NOT NULL,
    role TEXT, -- planner/writer/reviewer/revision/context/fact/embedding/rerank/image
    is_default BOOLEAN DEFAULT FALSE,
    config JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id);
CREATE INDEX IF NOT EXISTS idx_chapters_number ON chapters(book_id, number);
CREATE INDEX IF NOT EXISTS idx_characters_book ON characters(book_id);
CREATE INDEX IF NOT EXISTS idx_factions_book ON factions(book_id);
CREATE INDEX IF NOT EXISTS idx_locations_book ON locations(book_id);
CREATE INDEX IF NOT EXISTS idx_story_facts_book ON story_facts(book_id);
CREATE INDEX IF NOT EXISTS idx_story_facts_chapter ON story_facts(chapter_id);
CREATE INDEX IF NOT EXISTS idx_reviews_chapter ON reviews(chapter_id);
CREATE INDEX IF NOT EXISTS idx_review_issues_review ON review_issues(review_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_book ON tasks(book_id);
CREATE INDEX IF NOT EXISTS idx_operation_logs_created ON operation_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_foreshadows_book ON foreshadows(book_id);
CREATE INDEX IF NOT EXISTS idx_foreshadows_status ON foreshadows(status);
CREATE INDEX IF NOT EXISTS idx_timeline_events_book ON timeline_events(book_id);
CREATE INDEX IF NOT EXISTS idx_character_states_character ON character_states(character_id);
CREATE INDEX IF NOT EXISTS idx_character_states_chapter ON character_states(chapter_id);
"""


class MigrationError(RuntimeError):
    """Raised when an immutable schema migration cannot be safely applied."""


def _execute_sql_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute the repository's migration SQL inside the caller's transaction.

    ``sqlite3.Connection.executescript`` commits an open transaction before it
    runs.  These schema scripts deliberately contain no procedural SQL, so
    executing their semicolon-delimited statements preserves atomic migration
    semantics.
    """
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split()[0]
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


PHASE_1_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS migration_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    backup_manifest JSON,
    error_code TEXT,
    error_detail TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS legacy_imports (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE RESTRICT,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    migration_run_id TEXT REFERENCES migration_runs(id),
    imported_at TIMESTAMP,
    UNIQUE(project_id, source_fingerprint)
);

CREATE TABLE IF NOT EXISTS legacy_artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload JSON,
    provenance TEXT NOT NULL DEFAULT 'legacy',
    accepted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, source_path, content_hash)
);

CREATE TABLE IF NOT EXISTS story_states (
    book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    state JSON NOT NULL DEFAULT '{}',
    last_commit_id TEXT,
    state_version INTEGER NOT NULL DEFAULT 0,
    stale BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS story_projections (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    commit_id TEXT NOT NULL REFERENCES story_commits(id) ON DELETE CASCADE,
    projection_type TEXT NOT NULL DEFAULT 'story_state',
    payload JSON NOT NULL,
    applied_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, commit_id, projection_type)
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_lease ON tasks(lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_task_events_task_sequence ON task_events(task_id, sequence);
CREATE INDEX IF NOT EXISTS idx_story_projections_pending ON story_projections(applied_at);
CREATE INDEX IF NOT EXISTS idx_legacy_artifacts_project ON legacy_artifacts(project_id);
"""


PHASE_3_SCHEMA_SQL = """
CREATE INDEX IF NOT EXISTS idx_projects_source_kind ON projects(source_kind, migration_status);
"""


PHASE_4_SCHEMA_SQL = """
CREATE INDEX IF NOT EXISTS idx_reviews_chapter_version ON reviews(chapter_version_id);
"""


PHASE_5_MODEL_RUNTIME_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_model_routes (
    agent_role TEXT PRIMARY KEY,
    model_id TEXT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (agent_role IN ('planner', 'writer', 'reviewer', 'reviser', 'context',
                         'fact_extraction', 'embedding', 'rerank', 'image'))
);

CREATE TABLE IF NOT EXISTS generation_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    agent_role TEXT NOT NULL,
    provider_id TEXT NOT NULL REFERENCES model_providers(id) ON DELETE RESTRICT,
    model_id TEXT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
    prompt_key TEXT,
    prompt_version TEXT,
    input_reference JSON NOT NULL DEFAULT '{}',
    output_reference JSON,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    error_code TEXT,
    error_detail TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    CHECK (status IN ('running', 'succeeded', 'failed')),
    CHECK (agent_role IN ('planner', 'writer', 'reviewer', 'reviser', 'context',
                         'fact_extraction', 'embedding', 'rerank', 'image'))
);

CREATE INDEX IF NOT EXISTS idx_generation_runs_task ON generation_runs(task_id, started_at);
CREATE INDEX IF NOT EXISTS idx_generation_runs_model ON generation_runs(model_id, started_at);
CREATE INDEX IF NOT EXISTS idx_generation_runs_status ON generation_runs(status, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_providers_name ON model_providers(name);
"""


PHASE_6_MODEL_CREDENTIAL_CLEANUP_SQL = """
-- ``api_key`` is retained only as a legacy column for old schema readers.  It
-- must never contain a credential after the Phase 4 migration boundary.
UPDATE model_providers SET api_key = NULL WHERE api_key IS NOT NULL;
"""


PHASE_7_DOCUMENT_INGESTION_SCHEMA_SQL = """
CREATE INDEX IF NOT EXISTS idx_reference_documents_project_status
    ON reference_documents(project_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_index
    ON document_chunks(document_id, chunk_index);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reference_documents_fingerprint
    ON reference_documents(project_id, source_fingerprint);
"""


PHASE_8_STORY_BIBLE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS story_bible_workspaces (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'draft',
    current_step INTEGER NOT NULL DEFAULT 1,
    draft_version INTEGER NOT NULL DEFAULT 0,
    published_snapshot_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP,
    CHECK (status IN ('draft', 'published')),
    CHECK (current_step BETWEEN 1 AND 25)
);

CREATE TABLE IF NOT EXISTS story_bible_steps (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES story_bible_workspaces(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    step_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'empty',
    draft JSON NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'author',
    suggestion JSON,
    error_code TEXT,
    error_detail TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    confirmed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(workspace_id, step_number),
    UNIQUE(workspace_id, step_key),
    CHECK (status IN ('empty', 'draft', 'confirmed')),
    CHECK (source IN ('author', 'ai')),
    CHECK (step_number BETWEEN 1 AND 25)
);

CREATE TABLE IF NOT EXISTS story_bible_snapshots (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES story_bible_workspaces(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    payload JSON NOT NULL,
    checksum TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(workspace_id, version),
    CHECK (status IN ('draft', 'published'))
);

CREATE INDEX IF NOT EXISTS idx_story_bible_steps_workspace_status
    ON story_bible_steps(workspace_id, status, step_number);
CREATE INDEX IF NOT EXISTS idx_story_bible_snapshots_workspace_version
    ON story_bible_snapshots(workspace_id, version DESC);
"""

PHASE_9_EXPORT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    format TEXT NOT NULL DEFAULT 'md',
    file_path TEXT NOT NULL,
    file_size INTEGER,
    chapter_count INTEGER,
    word_count INTEGER,
    approved_only BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'completed',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_exports_project ON exports(project_id);
"""

PHASE_10_JOINT_REVIEW_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS joint_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    start_chapter INTEGER NOT NULL,
    end_chapter INTEGER NOT NULL,
    overall_score REAL,
    verdict TEXT,
    summary TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS joint_review_issues (
    id TEXT PRIMARY KEY,
    joint_review_id TEXT NOT NULL REFERENCES joint_reviews(id) ON DELETE CASCADE,
    chapter_numbers TEXT,
    dimension TEXT,
    severity TEXT DEFAULT 'major',
    description TEXT NOT NULL,
    suggestion TEXT,
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_joint_reviews_project ON joint_reviews(project_id);
"""

PHASE_11_PROMPT_REGISTRY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prompt_templates (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    task_type TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    user_template TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    is_default BOOLEAN NOT NULL DEFAULT 0,
    description TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prompt_templates_project ON prompt_templates(project_id);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_task_type ON prompt_templates(task_type);
"""

PHASE_12_CHARACTER_THEMES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS character_themes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    character_id TEXT,
    name TEXT NOT NULL,
    primary_color TEXT DEFAULT '#e94560',
    secondary_color TEXT DEFAULT '#0f3460',
    accent_color TEXT DEFAULT '#16213e',
    font_family TEXT DEFAULT 'serif',
    font_size TEXT DEFAULT '16px',
    is_default BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_character_themes_project ON character_themes(project_id);
CREATE INDEX IF NOT EXISTS idx_character_themes_character ON character_themes(character_id);
"""

PHASE_13_STORY_COMMIT_INTEGRITY_SQL = """
-- Deduplicate existing story_commits before adding UNIQUE index.
-- Keep the earliest commit for each (chapter_id, chapter_version_id) pair.
DELETE FROM story_commits
WHERE id NOT IN (
    SELECT MIN(id) FROM story_commits
    WHERE chapter_version_id IS NOT NULL
    GROUP BY chapter_id, chapter_version_id
) AND chapter_version_id IS NOT NULL;

-- Prevent duplicate commits for the same chapter version.
CREATE UNIQUE INDEX IF NOT EXISTS idx_story_commits_chapter_version
    ON story_commits(chapter_id, chapter_version_id)
    WHERE chapter_version_id IS NOT NULL;
"""


def _apply_v1(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, SCHEMA_SQL)
    row = conn.execute("SELECT version FROM db_version ORDER BY version DESC LIMIT 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO db_version(version) VALUES (?)", (DB_VERSION,))


def _apply_v2(conn: sqlite3.Connection) -> None:
    # Existing v1 databases are upgraded in place.  Defaults deliberately mark
    # database-only records as unmanaged; no user project is ever auto-adopted.
    for table, definition in (
        ("projects", "source_kind TEXT NOT NULL DEFAULT 'legacy_db'"),
        ("projects", "migration_status TEXT NOT NULL DEFAULT 'unmanaged'"),
        ("tasks", "project_id TEXT"),
        ("tasks", "stage TEXT"),
        ("tasks", "lease_owner TEXT"),
        ("tasks", "lease_expires_at TIMESTAMP"),
        ("tasks", "cancel_requested BOOLEAN NOT NULL DEFAULT FALSE"),
        ("tasks", "attempt INTEGER NOT NULL DEFAULT 0"),
        ("tasks", "next_attempt_at TIMESTAMP"),
        ("tasks", "error_code TEXT"),
        ("tasks", "event_sequence INTEGER NOT NULL DEFAULT 0"),
        ("tasks", "idempotency_key TEXT"),
        ("story_facts", "commit_id TEXT"),
        ("story_facts", "source TEXT NOT NULL DEFAULT 'native'"),
        ("story_facts", "verification_status TEXT NOT NULL DEFAULT 'verified'"),
        ("story_commits", "chapter_version_id TEXT"),
        ("story_commits", "accepted_at TIMESTAMP"),
        ("story_commits", "rejection_reason TEXT"),
    ):
        _add_column_if_missing(conn, table, definition)
    _execute_sql_script(conn, PHASE_1_SCHEMA_SQL)
    conn.execute("UPDATE projects SET source_kind = 'legacy_db' WHERE source_kind IS NULL OR source_kind = ''")
    conn.execute("UPDATE projects SET migration_status = 'unmanaged' WHERE migration_status IS NULL OR migration_status = ''")


def _apply_v3(conn: sqlite3.Connection) -> None:
    """Add native Book/Chapter core fields without adopting any file projects."""
    _add_column_if_missing(conn, "projects", "target_word_count INTEGER NOT NULL DEFAULT 0")
    _execute_sql_script(conn, PHASE_3_SCHEMA_SQL)


def _apply_v4(conn: sqlite3.Connection) -> None:
    """Associate reviews with the immutable ChapterVersion they inspected."""
    _add_column_if_missing(conn, "reviews", "chapter_version_id TEXT")
    _execute_sql_script(conn, PHASE_4_SCHEMA_SQL)


def _apply_v5(conn: sqlite3.Connection) -> None:
    """Add the persisted model runtime without ever writing raw credentials."""
    for table, definition in (
        ("model_providers", "credential_ref TEXT"),
        ("model_providers", "enabled BOOLEAN NOT NULL DEFAULT TRUE"),
        ("model_providers", "config JSON NOT NULL DEFAULT '{}'"),
        ("models", "capabilities JSON NOT NULL DEFAULT '[]'"),
        ("models", "enabled BOOLEAN NOT NULL DEFAULT TRUE"),
        ("models", "updated_at TIMESTAMP"),
    ):
        _add_column_if_missing(conn, table, definition)
    _execute_sql_script(conn, PHASE_5_MODEL_RUNTIME_SCHEMA_SQL)


def _apply_v6(conn: sqlite3.Connection) -> None:
    """Remove credentials left by the pre-Phase-4 schema after backup."""
    _execute_sql_script(conn, PHASE_6_MODEL_CREDENTIAL_CLEANUP_SQL)


def _apply_v7(conn: sqlite3.Connection) -> None:
    """Add durable document-ingestion metadata without rewriting attachments."""
    for table, definition in (
        ("reference_documents", "attachment_ref TEXT"),
        ("reference_documents", "source_fingerprint TEXT"),
        ("reference_documents", "mime_type TEXT"),
        ("reference_documents", "size_bytes INTEGER"),
        ("reference_documents", "status TEXT NOT NULL DEFAULT 'uploaded'"),
        ("reference_documents", "parser_version TEXT"),
        ("reference_documents", "metadata JSON NOT NULL DEFAULT '{}'"),
        ("reference_documents", "error_code TEXT"),
        ("reference_documents", "error_detail TEXT"),
        ("reference_documents", "ingestion_task_id TEXT"),
        ("reference_documents", "updated_at TIMESTAMP"),
        ("document_chunks", "start_char INTEGER NOT NULL DEFAULT 0"),
        ("document_chunks", "end_char INTEGER NOT NULL DEFAULT 0"),
        ("document_chunks", "checksum TEXT"),
        ("document_chunks", "metadata JSON NOT NULL DEFAULT '{}'"),
    ):
        _add_column_if_missing(conn, table, definition)
    conn.execute(
        """UPDATE reference_documents SET status='legacy', updated_at=COALESCE(updated_at, created_at)
           WHERE attachment_ref IS NULL AND (status IS NULL OR status='uploaded')"""
    )
    _execute_sql_script(conn, PHASE_7_DOCUMENT_INGESTION_SCHEMA_SQL)


def _apply_v8(conn: sqlite3.Connection) -> None:
    """Add durable Story Bible workspace, step, and snapshot state."""
    _execute_sql_script(conn, PHASE_8_STORY_BIBLE_SCHEMA_SQL)


def _apply_v9(conn: sqlite3.Connection) -> None:
    """Add export history tracking."""
    _execute_sql_script(conn, PHASE_9_EXPORT_SCHEMA_SQL)


def _apply_v10(conn: sqlite3.Connection) -> None:
    """Add joint review tables."""
    _execute_sql_script(conn, PHASE_10_JOINT_REVIEW_SCHEMA_SQL)


def _apply_v11(conn: sqlite3.Connection) -> None:
    """Add prompt registry tables."""
    _execute_sql_script(conn, PHASE_11_PROMPT_REGISTRY_SCHEMA_SQL)


def _apply_v12(conn: sqlite3.Connection) -> None:
    """Add character themes tables."""
    _execute_sql_script(conn, PHASE_12_CHARACTER_THEMES_SCHEMA_SQL)


def _apply_v13(conn: sqlite3.Connection) -> None:
    """Add UNIQUE constraint to story_commits to prevent duplicate commits."""
    _execute_sql_script(conn, PHASE_13_STORY_COMMIT_INTEGRITY_SQL)


PHASE_14_TASK_INTEGRITY_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency_key
    ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL;
"""


def _apply_v14(conn: sqlite3.Connection) -> None:
    """Add UNIQUE index on tasks.idempotency_key to prevent duplicate task creation."""
    _execute_sql_script(conn, PHASE_14_TASK_INTEGRITY_SQL)


class _Migration:
    def __init__(self, version: int, name: str, apply, source: str) -> None:
        self.version = version
        self.name = name
        self.apply = apply
        # The identifier and SQL definition are part of the persistent
        # contract. Changing either invalidates an already applied database.
        self.checksum = hashlib.sha256(f"{version}:{name}:{source}".encode("utf-8")).hexdigest()


_MIGRATIONS = (
    _Migration(1, "legacy_initial_schema", _apply_v1, SCHEMA_SQL),
    _Migration(2, "phase_1_authoritative_story_and_tasks", _apply_v2, PHASE_1_SCHEMA_SQL),
    _Migration(3, "phase_3_native_book_chapter_core", _apply_v3, PHASE_3_SCHEMA_SQL),
    _Migration(4, "phase_3_review_chapter_version_reference", _apply_v4, PHASE_4_SCHEMA_SQL),
    _Migration(5, "phase_4_model_gateway_router", _apply_v5, PHASE_5_MODEL_RUNTIME_SCHEMA_SQL),
    _Migration(6, "phase_4_model_credential_cleanup", _apply_v6, PHASE_6_MODEL_CREDENTIAL_CLEANUP_SQL),
    _Migration(7, "phase_5_document_ingestion", _apply_v7, PHASE_7_DOCUMENT_INGESTION_SCHEMA_SQL),
    _Migration(8, "phase_7_planning_story_bible", _apply_v8, PHASE_8_STORY_BIBLE_SCHEMA_SQL),
    _Migration(9, "phase_9_export_history", _apply_v9, PHASE_9_EXPORT_SCHEMA_SQL),
    _Migration(10, "phase_10_joint_review", _apply_v10, PHASE_10_JOINT_REVIEW_SCHEMA_SQL),
    _Migration(11, "phase_11_prompt_registry", _apply_v11, PHASE_11_PROMPT_REGISTRY_SCHEMA_SQL),
    _Migration(12, "phase_12_character_themes", _apply_v12, PHASE_12_CHARACTER_THEMES_SCHEMA_SQL),
    _Migration(13, "phase_13_story_commit_integrity", _apply_v13, PHASE_13_STORY_COMMIT_INTEGRITY_SQL),
    _Migration(14, "phase_14_task_idempotency_unique", _apply_v14, PHASE_14_TASK_INTEGRITY_SQL),
)


def generate_id() -> str:
    """生成唯一ID (完整32位hex，消除碰撞风险)"""
    return uuid.uuid4().hex


class Database:
    """数据库管理器"""
    
    def __init__(self, db_path: Optional[str] = None):
        """
        初始化数据库管理器
        
        Args:
            db_path: 数据库文件路径，默认为 projects/novelforge.db
        """
        if db_path is None:
            db_path = str(Path(__file__).parent.parent.parent / "projects" / "novelforge.db")
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 初始化数据库
        self._init_db()
    
    def _init_db(self):
        """Apply immutable, checksummed schema migrations.

        The previous release executed one large ``CREATE TABLE IF NOT EXISTS``
        script on every process start.  That made it impossible to tell which
        database shape a project actually had.  Migration 1 intentionally
        contains that historical shape; migration 2 is the Phase 1 authority
        and task-runtime upgrade.
        """
        self._backup_existing_database_before_migration()
        with self.connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            self._bootstrap_legacy_migration(conn)
            for migration in _MIGRATIONS:
                applied = conn.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = ?",
                    (migration.version,),
                ).fetchone()
                if applied:
                    if applied["checksum"] != migration.checksum:
                        raise MigrationError(
                            f"migration {migration.version} checksum mismatch; "
                            "applied migration files are immutable"
                        )
                    continue
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    migration.apply(conn)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)",
                        (migration.version, migration.checksum),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        logger.info(f"database migrations complete: {self.db_path}")

    def _backup_existing_database_before_migration(self) -> None:
        """Create an integrity-checked SQLite backup before changing an existing schema.

        Native story facts and task state share this database, so automatic schema
        migration must be recoverable even when it is not a legacy-project import.
        A SQLite online backup is used instead of a file copy to include any WAL
        state safely.  New empty databases have nothing to protect and skip this
        path entirely.
        """
        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return
        source_uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(source_uri, uri=True) as source:
            tables = {
                row[0] for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if not tables:
                return
            applied: set[int] = set()
            if "schema_migrations" in tables:
                applied = {
                    int(row[0]) for row in source.execute("SELECT version FROM schema_migrations")
                }
            if all(migration.version in applied for migration in _MIGRATIONS):
                return
            source_integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
            if source_integrity != "ok":
                raise MigrationError(
                    f"refusing schema migration because source integrity check failed: {source_integrity}"
                )

            backup_dir = self.db_path.parent / ".novelforge-backups" / "schema-migrations"
            backup_dir.mkdir(parents=True, exist_ok=True)
            run_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
            backup_path = backup_dir / f"{self.db_path.stem}-before-schema-{run_id}.sqlite3"
            with sqlite3.connect(backup_path) as destination:
                source.backup(destination)
                backup_integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if backup_integrity != "ok":
                backup_path.unlink(missing_ok=True)
                raise MigrationError(
                    f"schema migration backup integrity check failed: {backup_integrity}"
                )
            manifest_path = backup_dir / f"{backup_path.stem}.json"
            manifest = {
                "created_at": datetime.now().isoformat(),
                "source_database": str(self.db_path),
                "backup_database": backup_path.name,
                "source_tables": sorted(tables),
                "applied_migrations": sorted(applied),
                "source_integrity": source_integrity,
                "backup_integrity": backup_integrity,
                "backup_sha256": self._hash_file(backup_path),
            }
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            if not manifest_path.exists() or not self._hash_file(backup_path) == manifest["backup_sha256"]:
                raise MigrationError("schema migration backup verification failed")
            logger.info("verified schema-migration backup created: %s", backup_path)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _bootstrap_legacy_migration(self, conn: sqlite3.Connection) -> None:
        """Record the known v1 schema for a database made before this runner."""
        row = conn.execute("SELECT 1 FROM schema_migrations WHERE version = 1").fetchone()
        if row:
            return
        legacy = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'db_version'"
        ).fetchone()
        if not legacy:
            return
        projects = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
        ).fetchone()
        if not projects:
            raise MigrationError("legacy db_version exists but the v1 projects table is missing")
        conn.execute(
            "INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)",
            (1, _MIGRATIONS[0].checksum),
        )
        conn.commit()
    
    @contextmanager
    def connect(self):
        """获取数据库连接的上下文管理器"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        """Yield one foreign-key-enforced connection with all-or-nothing writes."""
        with self.connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """执行SQL语句"""
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor
    
    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """批量执行SQL语句"""
        with self.connect() as conn:
            return conn.executemany(sql, params_list)
    
    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        """查询单条记录"""
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict]:
        """查询多条记录"""
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    
    def insert(self, table: str, data: Dict) -> str:
        """插入记录，返回ID"""
        if 'id' not in data:
            data['id'] = generate_id()
        
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?'] * len(data))
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        
        with self.connect() as conn:
            conn.execute(sql, tuple(data.values()))
            conn.commit()
        
        return data['id']
    
    def update(self, table: str, data: Dict, where: str, where_params: tuple) -> int:
        """更新记录"""
        # Some append-only audit tables intentionally have no ``updated_at``.
        # The generic legacy helper remains compatible without manufacturing a
        # column those tables do not own.
        if "updated_at" not in data:
            columns = {row["name"] for row in self.fetchall(f"PRAGMA table_info({table})")}
            if "updated_at" in columns:
                data["updated_at"] = datetime.now().isoformat()
        set_clause = ', '.join([f"{k} = ?" for k in data.keys()])
        sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
        
        with self.connect() as conn:
            cursor = conn.execute(sql, tuple(data.values()) + where_params)
            conn.commit()
            return cursor.rowcount
    
    def delete(self, table: str, where: str, where_params: tuple) -> int:
        """删除记录"""
        sql = f"DELETE FROM {table} WHERE {where}"
        
        with self.connect() as conn:
            cursor = conn.execute(sql, where_params)
            conn.commit()
            return cursor.rowcount
    
    def get_by_id(self, table: str, id: str) -> Optional[Dict]:
        """根据ID获取记录"""
        return self.fetchone(f"SELECT * FROM {table} WHERE id = ?", (id,))
    
    def list_all(self, table: str, where: str = "", params: tuple = (), 
                 order_by: str = "created_at DESC", limit: int = 100) -> List[Dict]:
        """列出记录"""
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order_by} LIMIT {limit}"
        return self.fetchall(sql, params)
    
    def count(self, table: str, where: str = "", params: tuple = ()) -> int:
        """统计记录数"""
        sql = f"SELECT COUNT(*) as cnt FROM {table}"
        if where:
            sql += f" WHERE {where}"
        result = self.fetchone(sql, params)
        return result['cnt'] if result else 0
    
    def table_exists(self, table: str) -> bool:
        """检查表是否存在"""
        result = self.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        )
        return result is not None
    
    def get_version(self) -> int:
        """获取数据库版本"""
        result = self.fetchone("SELECT version FROM db_version ORDER BY version DESC LIMIT 1")
        return result['version'] if result else 0
    
    def backup(self, backup_path: str):
        """备份数据库"""
        import shutil
        shutil.copy2(str(self.db_path), backup_path)
        logger.info(f"数据库备份完成: {backup_path}")
    
    def vacuum(self):
        """压缩数据库"""
        with self.connect() as conn:
            conn.execute("VACUUM")
        logger.info("数据库压缩完成")


# 全局数据库实例
_db_instance: Optional[Database] = None


def get_db() -> Database:
    """获取全局数据库实例"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance


def init_db(db_path: Optional[str] = None) -> Database:
    """初始化数据库"""
    global _db_instance
    _db_instance = Database(db_path)
    return _db_instance


def get_backup_manager():
    """获取备份管理器实例"""
    from .backup import get_backup_manager as _get_backup_manager
    return _get_backup_manager()


def init_backup_manager(workspace_root=None):
    """初始化备份管理器"""
    from .backup import init_backup_manager as _init_backup_manager
    return _init_backup_manager(workspace_root)
