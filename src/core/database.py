"""
NovelForge 数据库管理模块
提供数据库初始化、迁移和基础 CRUD 操作
"""

import sqlite3
import uuid
import hashlib
import json
import threading as _threading
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
    runs. ``sqlite3.complete_statement`` preserves the caller's transaction
    while also correctly handling trigger bodies containing semicolons.
    """
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                conn.execute(statement)
            statement = ""
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


PHASE_15_PLOT_WORKSPACE_SQL = """
CREATE TABLE IF NOT EXISTS plot_workspaces (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL UNIQUE REFERENCES books(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 1,
    graph JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plot_workspace_revisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES plot_workspaces(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    graph JSON NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(workspace_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_plot_workspace_revisions_workspace
    ON plot_workspace_revisions(workspace_id, revision);
"""


def _apply_v15(conn: sqlite3.Connection) -> None:
    """Add per-book planning settings and a durable editable plot canvas."""
    _add_column_if_missing(conn, "projects", "target_volumes INTEGER NOT NULL DEFAULT 5")
    _add_column_if_missing(conn, "projects", "style_profile JSON NOT NULL DEFAULT '{}'")
    _execute_sql_script(conn, PHASE_15_PLOT_WORKSPACE_SQL)


PHASE_16_CREATION_WORKFLOW_SQL = """
CREATE TABLE IF NOT EXISTS creation_workflows (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'planned',
    status TEXT NOT NULL DEFAULT 'planning',
    seed TEXT NOT NULL DEFAULT '',
    metadata JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS planning_sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    checksum TEXT NOT NULL,
    metadata JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, source_type, checksum)
);

CREATE INDEX IF NOT EXISTS idx_planning_sources_project
    ON planning_sources(project_id, source_type, created_at);

CREATE TABLE IF NOT EXISTS thought_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'questioning',
    seed TEXT NOT NULL DEFAULT '',
    turns JSON NOT NULL DEFAULT '[]',
    current_question TEXT NOT NULL DEFAULT '',
    question_index INTEGER NOT NULL DEFAULT 0,
    framework JSON NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS story_architecture_views (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    snapshot_id TEXT,
    view_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    payload JSON NOT NULL DEFAULT '{}',
    source_manifest JSON NOT NULL DEFAULT '[]',
    generated_by TEXT NOT NULL DEFAULT 'planning-materials-projection',
    readonly INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, view_type)
);

CREATE TABLE IF NOT EXISTS forecast_imports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_task_id TEXT,
    target TEXT NOT NULL DEFAULT 'canvas',
    branch JSON NOT NULL DEFAULT '{}',
    canvas_revision INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_forecast_imports_project
    ON forecast_imports(project_id, created_at);
"""


def _apply_v16(conn: sqlite3.Connection) -> None:
    """Add durable creation modes, planning inputs, read-only projections, and forecast audit."""
    _execute_sql_script(conn, PHASE_16_CREATION_WORKFLOW_SQL)


PHASE_17_AGENT_EXTENSIONS_SQL = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    transport TEXT NOT NULL DEFAULT 'stdio',
    command TEXT NOT NULL DEFAULT '',
    args JSON NOT NULL DEFAULT '[]',
    url TEXT NOT NULL DEFAULT '',
    environment JSON NOT NULL DEFAULT '{}',
    headers JSON NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    config JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (transport IN ('stdio', 'sse', 'streamable_http'))
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_enabled
    ON mcp_servers(enabled, name);

CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_key
    ON skills(key) WHERE key IS NOT NULL AND key <> '';
"""


def _apply_v17(conn: sqlite3.Connection) -> None:
    """Add editable Agent prompts and durable user extension registries."""
    _add_column_if_missing(conn, "agent_model_routes", "system_prompt TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "agent_model_routes", "system_prompt_version INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "skills", "key TEXT")
    _add_column_if_missing(conn, "skills", "version INTEGER NOT NULL DEFAULT 1")
    _add_column_if_missing(conn, "skills", "source TEXT NOT NULL DEFAULT 'builtin'")
    _add_column_if_missing(conn, "skills", "config JSON NOT NULL DEFAULT '{}'")
    _execute_sql_script(conn, PHASE_17_AGENT_EXTENSIONS_SQL)


PHASE_18_AGENT_EXTENSION_SCOPE_SQL = """
CREATE TABLE IF NOT EXISTS agent_extension_overrides (
    -- Project IDs also identify legacy file-backed novels, so this scope table
    -- intentionally keeps the string reference without a foreign key.
    project_id TEXT NOT NULL,
    extension_type TEXT NOT NULL,
    extension_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(project_id, extension_type, extension_id),
    CHECK (extension_type IN ('skill', 'mcp'))
);

CREATE INDEX IF NOT EXISTS idx_agent_extension_overrides_project
    ON agent_extension_overrides(project_id, extension_type, enabled);
"""


def _apply_v18(conn: sqlite3.Connection) -> None:
    """Add per-project enablement overrides for global Agent extensions."""
    _execute_sql_script(conn, PHASE_18_AGENT_EXTENSION_SCOPE_SQL)


PHASE_19_DRAFT_IMPORT_ANALYSIS_SQL = """
CREATE TABLE IF NOT EXISTS draft_imports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    story_bible_document_id TEXT,
    language_plan_document_id TEXT,
    draft_document_ids JSON NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'uploaded',
    task_id TEXT,
    report JSON NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_detail TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('uploaded', 'running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_draft_imports_project
    ON draft_imports(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_draft_imports_task
    ON draft_imports(task_id);
"""


def _apply_v19(conn: sqlite3.Connection) -> None:
    """Persist imported draft batches and their model analysis reports."""
    _execute_sql_script(conn, PHASE_19_DRAFT_IMPORT_ANALYSIS_SQL)


PHASE_20_CONTINUOUS_RUN_GOVERNANCE_SQL = """
-- A continuous parent releases its lease while an independently queued child
-- is executing.  The runtime uses this pointer to wake the parent only after
-- the child reaches a terminal state.
"""


def _apply_v20(conn: sqlite3.Connection) -> None:
    """Add durable parent-child waiting and auditable author overrides."""
    _add_column_if_missing(conn, "tasks", "waiting_for_task_id TEXT")
    _add_column_if_missing(conn, "story_commits", "author_override BOOLEAN NOT NULL DEFAULT FALSE")
    _add_column_if_missing(conn, "story_commits", "override_reason TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_waiting_for_task ON tasks(waiting_for_task_id)"
    )


PHASE_21_NARRATIVE_OS_CLOSURE_SQL = """
-- An accepted StoryCommit is copied into this append-only event ledger.  The
-- ledger is the replay boundary. Mutable read models never become authority.
CREATE TABLE IF NOT EXISTS narrative_events (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    commit_id TEXT NOT NULL REFERENCES story_commits(id) ON DELETE RESTRICT,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE RESTRICT,
    chapter_version_id TEXT,
    review_id TEXT,
    event_type TEXT NOT NULL DEFAULT 'story_commit_accepted',
    payload JSON NOT NULL,
    event_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, sequence),
    UNIQUE(commit_id),
    UNIQUE(event_hash)
);

CREATE INDEX IF NOT EXISTS idx_narrative_events_book_sequence
    ON narrative_events(book_id, sequence);
CREATE INDEX IF NOT EXISTS idx_narrative_events_commit
    ON narrative_events(commit_id);

-- Every rebuildable projection records the event boundary it observed.  A
-- failed or stale row is visible to recovery and cannot be mistaken for
-- successful materialization.
CREATE TABLE IF NOT EXISTS projection_ledger (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    source_event_id TEXT NOT NULL REFERENCES narrative_events(id) ON DELETE RESTRICT,
    projection_type TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    projection_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error_code TEXT,
    error_detail TEXT,
    applied_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, source_event_id, projection_type),
    CHECK(status IN ('pending', 'applied', 'failed', 'stale', 'degraded'))
);

CREATE INDEX IF NOT EXISTS idx_projection_ledger_book_status
    ON projection_ledger(book_id, projection_type, status);

-- Canonical Memory projection.  Legacy file-backed MemorySystem remains a
-- compatibility adapter only. New writes land here and carry provenance.
CREATE TABLE IF NOT EXISTS narrative_memory (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    source_event_id TEXT NOT NULL REFERENCES narrative_events(id) ON DELETE RESTRICT,
    source_commit_id TEXT NOT NULL REFERENCES story_commits(id) ON DELETE RESTRICT,
    source_version_id TEXT,
    category TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'story',
    content TEXT NOT NULL,
    entity_refs JSON NOT NULL DEFAULT '[]',
    importance REAL NOT NULL DEFAULT 0.5,
    valid_from_chapter INTEGER,
    valid_to_chapter INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    provenance JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_event_id, category, content),
    CHECK(status IN ('active', 'invalidated', 'superseded'))
);

CREATE INDEX IF NOT EXISTS idx_narrative_memory_active
    ON narrative_memory(book_id, status, category, valid_from_chapter);

-- Durable vector materialization.  The vector is opaque JSON so providers
-- can choose dimensions without coupling the Canon schema to one SDK.
CREATE TABLE IF NOT EXISTS embedding_projections (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    content TEXT NOT NULL,
    content_checksum TEXT NOT NULL,
    model_key TEXT NOT NULL,
    embedding JSON,
    dimension INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    projection_version TEXT NOT NULL,
    provenance JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, source_type, source_id, source_version, model_key),
    CHECK(status IN ('pending', 'ready', 'failed', 'stale', 'degraded'))
);

CREATE INDEX IF NOT EXISTS idx_embedding_projections_lookup
    ON embedding_projections(book_id, status, model_key);
"""


def _apply_v21(conn: sqlite3.Connection) -> None:
    """Add the replayable Canon and durable derived-projection boundaries."""
    for table, definition in (
        ("story_commits", "review_id TEXT"),
        ("story_commits", "source_fingerprint TEXT"),
        ("story_commits", "event_hash TEXT"),
        ("story_commits", "override_provenance JSON NOT NULL DEFAULT '{}'"),
    ):
        _add_column_if_missing(conn, table, definition)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_commits_review ON story_commits(review_id, chapter_version_id)"
    )
    _execute_sql_script(conn, PHASE_21_NARRATIVE_OS_CLOSURE_SQL)
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS narrative_events_immutable_update
           BEFORE UPDATE ON narrative_events
           BEGIN SELECT RAISE(ABORT, 'narrative events are immutable'); END"""
    )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS narrative_events_immutable_delete
           BEFORE DELETE ON narrative_events
           BEGIN SELECT RAISE(ABORT, 'narrative events are immutable'); END"""
    )


PHASE_22_COMMIT_REBASE_SQL = """
-- A historical edit supersedes the old commit for a ChapterVersion.  The
-- version may then be reviewed and committed again while pending/accepted
-- idempotency remains enforced by the partial unique index.
DROP INDEX IF EXISTS idx_story_commits_chapter_version;
CREATE UNIQUE INDEX IF NOT EXISTS idx_story_commits_chapter_version_live
    ON story_commits(chapter_id, chapter_version_id)
    WHERE chapter_version_id IS NOT NULL AND status IN ('pending', 'accepted');
"""


def _apply_v22(conn: sqlite3.Connection) -> None:
    """Allow a superseded ChapterVersion to be re-reviewed after a rebase."""
    _execute_sql_script(conn, PHASE_22_COMMIT_REBASE_SQL)


PHASE_23_NARRATIVE_RUNTIME_V2_SQL = """
-- Durable GenerationAttempt records make provider calls recoverable at the
-- boundary between the provider response and the following projection.
CREATE TABLE IF NOT EXISTS generation_attempts (
    id TEXT PRIMARY KEY,
    generation_run_id TEXT NOT NULL REFERENCES generation_runs(id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    task_stage TEXT NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    provider_id TEXT,
    model_id TEXT,
    prompt_key TEXT,
    prompt_version TEXT,
    prompt_hash TEXT,
    context_hash TEXT,
    request_started_at TIMESTAMP,
    provider_response_received_at TIMESTAMP,
    response_hash TEXT,
    response_artifact JSON,
    usage JSON NOT NULL DEFAULT '{}',
    latency_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'prepared',
    error_code TEXT,
    error_detail TEXT,
    consumed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(status IN ('prepared', 'requesting', 'response_received', 'persisted',
                     'consumed', 'failed', 'abandoned'))
);

CREATE INDEX IF NOT EXISTS idx_generation_attempts_task
    ON generation_attempts(task_id, task_stage, created_at);
CREATE INDEX IF NOT EXISTS idx_generation_attempts_request_hash
    ON generation_attempts(request_hash, status);

CREATE TABLE IF NOT EXISTS canonical_imports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_document_ids JSON NOT NULL DEFAULT '[]',
    source_fingerprint TEXT NOT NULL,
    manifest JSON NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'proposed',
    version INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT NOT NULL UNIQUE,
    accepted_event_id TEXT,
    task_id TEXT,
    report JSON NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_detail TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(status IN ('proposed', 'partially_accepted', 'accepted', 'rejected', 'failed'))
);

CREATE TABLE IF NOT EXISTS canonical_import_items (
    id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL REFERENCES canonical_imports(id) ON DELETE CASCADE,
    item_type TEXT NOT NULL,
    source_document_id TEXT,
    source_start INTEGER NOT NULL DEFAULT 0,
    source_end INTEGER NOT NULL DEFAULT 0,
    chapter_number INTEGER,
    proposed_value JSON NOT NULL DEFAULT '{}',
    edited_value JSON,
    confidence REAL NOT NULL DEFAULT 0.0,
    conflict JSON NOT NULL DEFAULT '{}',
    provenance JSON NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'proposed',
    accepted_event_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(status IN ('proposed', 'accepted', 'edited', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_canonical_import_items_import
    ON canonical_import_items(import_id, status, source_start, id);
CREATE INDEX IF NOT EXISTS idx_canonical_imports_project
    ON canonical_imports(project_id, created_at DESC);
"""


def _apply_v23(conn: sqlite3.Connection) -> None:
    """Upgrade the closure ledger and add durable runtime/import boundaries.

    SQLite cannot drop a UNIQUE constraint in place.  The event and its two
    event-owned projections are therefore rebuilt in one migration transaction;
    all rows and their identifiers are copied before the v2 shape is exposed.
    """
    conn.execute("DROP TRIGGER IF EXISTS narrative_events_immutable_update")
    conn.execute("DROP TRIGGER IF EXISTS narrative_events_immutable_delete")
    conn.execute("CREATE TEMP TABLE _v23_events AS SELECT * FROM narrative_events")
    conn.execute("CREATE TEMP TABLE _v23_projection_ledger AS SELECT * FROM projection_ledger")
    conn.execute("CREATE TEMP TABLE _v23_narrative_memory AS SELECT * FROM narrative_memory")
    conn.execute("DROP TABLE projection_ledger")
    conn.execute("DROP TABLE narrative_memory")
    conn.execute("DROP TABLE narrative_events")

    _execute_sql_script(conn,
        """
        CREATE TABLE narrative_events (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            -- commit_id is retained for compatibility with the first closure
            -- release, because lifecycle events may legitimately leave it NULL.
            commit_id TEXT REFERENCES story_commits(id) ON DELETE RESTRICT,
            chapter_id TEXT REFERENCES chapters(id) ON DELETE RESTRICT,
            chapter_version_id TEXT,
            review_id TEXT,
            event_type TEXT NOT NULL,
            payload JSON NOT NULL,
            event_hash TEXT NOT NULL,
            source_event_id TEXT,
            source_commit_id TEXT REFERENCES story_commits(id) ON DELETE RESTRICT,
            aggregate_type TEXT NOT NULL DEFAULT 'chapter',
            aggregate_id TEXT,
            reason TEXT NOT NULL DEFAULT '',
            actor_type TEXT NOT NULL DEFAULT 'system',
            actor_id TEXT,
            actor_scope TEXT NOT NULL DEFAULT 'book',
            source_fingerprint TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(book_id, sequence),
            UNIQUE(event_hash)
        );

        CREATE INDEX idx_narrative_events_book_sequence
            ON narrative_events(book_id, sequence);
        CREATE INDEX idx_narrative_events_commit
            ON narrative_events(commit_id);
        CREATE INDEX idx_narrative_events_source_commit
            ON narrative_events(book_id, source_commit_id, sequence);
        CREATE INDEX idx_narrative_events_aggregate
            ON narrative_events(book_id, aggregate_type, aggregate_id, sequence);

        CREATE TABLE projection_ledger (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            source_event_id TEXT NOT NULL REFERENCES narrative_events(id) ON DELETE RESTRICT,
            projection_type TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            projection_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error_code TEXT,
            error_detail TEXT,
            applied_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(book_id, source_event_id, projection_type),
            CHECK(status IN ('pending', 'applied', 'failed', 'stale', 'degraded'))
        );

        CREATE INDEX idx_projection_ledger_book_status
            ON projection_ledger(book_id, projection_type, status);

        CREATE TABLE narrative_memory (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            source_event_id TEXT NOT NULL REFERENCES narrative_events(id) ON DELETE RESTRICT,
            source_commit_id TEXT NOT NULL REFERENCES story_commits(id) ON DELETE RESTRICT,
            source_version_id TEXT,
            category TEXT NOT NULL,
            memory_type TEXT NOT NULL DEFAULT 'episodic',
            compression_version TEXT NOT NULL DEFAULT 'none',
            compiler_version TEXT NOT NULL DEFAULT 'memory-compiler-v1',
            generation_run_id TEXT,
            scope TEXT NOT NULL DEFAULT 'story',
            content TEXT NOT NULL,
            entity_refs JSON NOT NULL DEFAULT '[]',
            importance REAL NOT NULL DEFAULT 0.5,
            valid_from_chapter INTEGER,
            valid_to_chapter INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            stale_reason TEXT,
            provenance JSON NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_event_id, category, content),
            CHECK(status IN ('active', 'invalidated', 'superseded'))
        );

        CREATE INDEX idx_narrative_memory_active
            ON narrative_memory(book_id, status, category, valid_from_chapter);
        CREATE INDEX idx_narrative_memory_compiler
            ON narrative_memory(book_id, compiler_version, status);

        """
    )
    conn.execute(
        """CREATE TRIGGER narrative_events_immutable_update
           BEFORE UPDATE ON narrative_events
           BEGIN SELECT RAISE(ABORT, 'narrative events are immutable'); END"""
    )
    conn.execute(
        """CREATE TRIGGER narrative_events_immutable_delete
           BEFORE DELETE ON narrative_events
           BEGIN SELECT RAISE(ABORT, 'narrative events are immutable'); END"""
    )
    conn.execute(
        """INSERT INTO narrative_events(
               id, book_id, sequence, commit_id, chapter_id, chapter_version_id,
               review_id, event_type, payload, event_hash, source_commit_id,
               aggregate_type, aggregate_id, source_fingerprint, created_at
           )
           SELECT e.id, e.book_id, e.sequence, e.commit_id, e.chapter_id,
                  e.chapter_version_id, e.review_id, e.event_type, e.payload,
                  e.event_hash, e.commit_id, 'chapter', e.chapter_id,
                  COALESCE(sc.source_fingerprint, ''), e.created_at
           FROM _v23_events e LEFT JOIN story_commits sc ON sc.id=e.commit_id"""
    )
    conn.execute(
        """INSERT INTO projection_ledger(
               id, book_id, source_event_id, projection_type, source_fingerprint,
               projection_version, status, error_code, error_detail, applied_at,
               created_at, updated_at
           ) SELECT id, book_id, source_event_id, projection_type,
                    source_fingerprint, projection_version, status, error_code,
                    error_detail, applied_at, created_at, updated_at
             FROM _v23_projection_ledger"""
    )
    conn.execute(
        """INSERT INTO narrative_memory(
               id, book_id, source_event_id, source_commit_id, source_version_id,
               category, scope, content, entity_refs, importance, valid_from_chapter,
               valid_to_chapter, status, provenance, created_at, updated_at
           ) SELECT id, book_id, source_event_id, source_commit_id, source_version_id,
                    category, scope, content, entity_refs, importance, valid_from_chapter,
                    valid_to_chapter, status, provenance, created_at, updated_at
             FROM _v23_narrative_memory"""
    )
    conn.execute("DROP TABLE _v23_events")
    conn.execute("DROP TABLE _v23_projection_ledger")
    conn.execute("DROP TABLE _v23_narrative_memory")

    _add_column_if_missing(conn, "reviews", "idempotency_key TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_idempotency_key "
        "ON reviews(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    for table, definition in (
        ("character_states", "source_event_id TEXT"),
        ("character_states", "source_commit_id TEXT"),
        ("character_states", "projection_version TEXT"),
        ("faction_states", "source_event_id TEXT"),
        ("faction_states", "source_commit_id TEXT"),
        ("faction_states", "projection_version TEXT"),
        ("location_states", "source_event_id TEXT"),
        ("location_states", "source_commit_id TEXT"),
        ("location_states", "projection_version TEXT"),
        ("relationships", "source_event_id TEXT"),
        ("relationships", "source_commit_id TEXT"),
        ("relationships", "projection_version TEXT"),
        ("timeline_events", "source_event_id TEXT"),
        ("timeline_events", "source_commit_id TEXT"),
        ("timeline_events", "projection_version TEXT"),
        ("foreshadows", "source_event_id TEXT"),
        ("foreshadows", "source_commit_id TEXT"),
        ("foreshadows", "projection_version TEXT"),
        ("hooks", "source_event_id TEXT"),
        ("hooks", "source_commit_id TEXT"),
        ("hooks", "projection_version TEXT"),
    ):
        _add_column_if_missing(conn, table, definition)
    _execute_sql_script(conn, PHASE_23_NARRATIVE_RUNTIME_V2_SQL)


PHASE_24_STORYFLOW_SIMULATION_FOUNDATION_SQL = """
CREATE TABLE IF NOT EXISTS simulation_world_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    base_canon_event_id TEXT NOT NULL,
    canon_hash TEXT NOT NULL,
    story_state_version INTEGER NOT NULL,
    planning_snapshot_id TEXT,
    planning_snapshot_hash TEXT,
    snapshot_version INTEGER NOT NULL,
    world_payload JSON NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(book_id, base_canon_event_id, canon_hash, planning_snapshot_hash)
);

CREATE INDEX IF NOT EXISTS idx_simulation_world_snapshots_book_created
    ON simulation_world_snapshots(book_id, created_at DESC);
"""


def _apply_v24(conn: sqlite3.Connection) -> None:
    """Add the immutable Canon-to-simulation world snapshot boundary."""
    _execute_sql_script(conn, PHASE_24_STORYFLOW_SIMULATION_FOUNDATION_SQL)


PHASE_25_STORYFLOW_SIMULATION_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS simulation_runs (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    snapshot_id TEXT NOT NULL REFERENCES simulation_world_snapshots(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    current_round INTEGER NOT NULL DEFAULT 0,
    max_rounds INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS simulation_events (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    simulation_time TEXT,
    event_type TEXT NOT NULL,
    actor_type TEXT,
    actor_id TEXT,
    target_ids JSON NOT NULL DEFAULT '[]',
    action_id TEXT,
    payload JSON NOT NULL DEFAULT '{}',
    state_delta JSON NOT NULL DEFAULT '{}',
    visibility_scope TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(simulation_run_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_simulation_runs_book_created
    ON simulation_runs(book_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_simulation_events_run_sequence
    ON simulation_events(simulation_run_id, sequence);
"""


def _apply_v25(conn: sqlite3.Connection) -> None:
    """Add durable runs and the append-only counterfactual event ledger."""
    _execute_sql_script(conn, PHASE_25_STORYFLOW_SIMULATION_LEDGER_SQL)


PHASE_26_STORYFLOW_SIMULATION_INTEGRITY_SQL = """
CREATE TRIGGER IF NOT EXISTS prevent_simulation_snapshot_update
BEFORE UPDATE ON simulation_world_snapshots
BEGIN
    SELECT RAISE(ABORT, 'simulation world snapshots are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_snapshot_delete
BEFORE DELETE ON simulation_world_snapshots
BEGIN
    SELECT RAISE(ABORT, 'simulation world snapshots are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_event_update
BEFORE UPDATE ON simulation_events
BEGIN
    SELECT RAISE(ABORT, 'simulation events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_event_delete
BEFORE DELETE ON simulation_events
BEGIN
    SELECT RAISE(ABORT, 'simulation events are append-only');
END;
"""


def _apply_v26(conn: sqlite3.Connection) -> None:
    """Enforce immutable snapshot and event-ledger rows in SQLite itself."""
    _execute_sql_script(conn, PHASE_26_STORYFLOW_SIMULATION_INTEGRITY_SQL)


PHASE_27_STORYFLOW_SIMULATION_CHECKPOINT_SQL = """
CREATE TABLE IF NOT EXISTS simulation_checkpoints (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    event_sequence INTEGER NOT NULL,
    state_hash TEXT NOT NULL,
    state_values JSON NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(simulation_run_id, event_sequence)
);

CREATE INDEX IF NOT EXISTS idx_simulation_checkpoints_run_created
    ON simulation_checkpoints(simulation_run_id, created_at DESC);

CREATE TRIGGER IF NOT EXISTS prevent_simulation_checkpoint_update
BEFORE UPDATE ON simulation_checkpoints
BEGIN
    SELECT RAISE(ABORT, 'simulation checkpoints are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_checkpoint_delete
BEFORE DELETE ON simulation_checkpoints
BEGIN
    SELECT RAISE(ABORT, 'simulation checkpoints are immutable');
END;
"""


def _apply_v27(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, PHASE_27_STORYFLOW_SIMULATION_CHECKPOINT_SQL)


PHASE_28_STORYFLOW_AGENT_MEMORY_SQL = """
CREATE TABLE IF NOT EXISTS simulation_agent_memories (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    agent_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content JSON NOT NULL,
    source_simulation_event_ids JSON NOT NULL DEFAULT '[]',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,
    validity TEXT NOT NULL DEFAULT 'active',
    created_round INTEGER NOT NULL DEFAULT 0,
    last_accessed_round INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_simulation_agent_memory_scope
    ON simulation_agent_memories(simulation_run_id, agent_id, memory_type, importance DESC);
"""


def _apply_v28(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, PHASE_28_STORYFLOW_AGENT_MEMORY_SQL)


PHASE_29_STORYFLOW_SIMULATION_BRANCHES_SQL = """
CREATE TABLE IF NOT EXISTS simulation_branches (
    id TEXT PRIMARY KEY,
    parent_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    branch_run_id TEXT NOT NULL UNIQUE REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    fork_sequence INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_simulation_branches_parent
    ON simulation_branches(parent_run_id, fork_sequence);
"""


def _apply_v29(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, PHASE_29_STORYFLOW_SIMULATION_BRANCHES_SQL)


PHASE_30_STORYFLOW_SIMULATION_INTERVENTIONS_SQL = """
CREATE TABLE IF NOT EXISTS simulation_interventions (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    state_delta JSON NOT NULL,
    rationale TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE REFERENCES simulation_events(id) ON DELETE RESTRICT,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_simulation_interventions_run
    ON simulation_interventions(simulation_run_id, created_at DESC);
"""


def _apply_v30(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, PHASE_30_STORYFLOW_SIMULATION_INTERVENTIONS_SQL)


PHASE_31_STORYFLOW_SIMULATION_ADOPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS simulation_adoptions (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    payload JSON NOT NULL,
    status TEXT NOT NULL,
    planning_node_id TEXT,
    planning_revision INTEGER,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_simulation_adoptions_run ON simulation_adoptions(simulation_run_id, created_at DESC);
"""


def _apply_v31(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, PHASE_31_STORYFLOW_SIMULATION_ADOPTIONS_SQL)


PHASE_32_STORYFLOW_SIMULATION_ANALYSIS_SQL = """
CREATE TABLE IF NOT EXISTS simulation_analysis_reports (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_simulation_analysis_reports_run_created
    ON simulation_analysis_reports(simulation_run_id, created_at DESC);

CREATE TRIGGER IF NOT EXISTS prevent_simulation_analysis_report_update
BEFORE UPDATE ON simulation_analysis_reports
BEGIN
    SELECT RAISE(ABORT, 'simulation analysis reports are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_analysis_report_delete
BEFORE DELETE ON simulation_analysis_reports
BEGIN
    SELECT RAISE(ABORT, 'simulation analysis reports are immutable');
END;
"""


def _apply_v32(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, PHASE_32_STORYFLOW_SIMULATION_ANALYSIS_SQL)


PHASE_33_STORYFLOW_CHARACTER_INTERACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS simulation_character_interactions (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    agent_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    response TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_simulation_character_interactions_agent
    ON simulation_character_interactions(simulation_run_id, agent_id, created_at DESC);

CREATE TRIGGER IF NOT EXISTS prevent_simulation_character_interaction_update
BEFORE UPDATE ON simulation_character_interactions
BEGIN
    SELECT RAISE(ABORT, 'simulation character interactions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_character_interaction_delete
BEFORE DELETE ON simulation_character_interactions
BEGIN
    SELECT RAISE(ABORT, 'simulation character interactions are immutable');
END;
"""


def _apply_v33(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, PHASE_33_STORYFLOW_CHARACTER_INTERACTIONS_SQL)


PHASE_34_STORYFLOW_SURVEYS_SQL = """
CREATE TABLE IF NOT EXISTS simulation_surveys (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    question TEXT NOT NULL,
    agent_ids JSON NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS simulation_survey_responses (
    id TEXT PRIMARY KEY,
    survey_id TEXT NOT NULL REFERENCES simulation_surveys(id) ON DELETE RESTRICT,
    agent_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL UNIQUE REFERENCES simulation_character_interactions(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    response TEXT NOT NULL,
    evidence JSON NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_simulation_surveys_run_created
    ON simulation_surveys(simulation_run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_simulation_survey_responses_survey
    ON simulation_survey_responses(survey_id, agent_id);

CREATE TRIGGER IF NOT EXISTS prevent_simulation_survey_response_update
BEFORE UPDATE ON simulation_survey_responses
BEGIN
    SELECT RAISE(ABORT, 'simulation survey responses are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_survey_response_delete
BEFORE DELETE ON simulation_survey_responses
BEGIN
    SELECT RAISE(ABORT, 'simulation survey responses are immutable');
END;
"""


def _apply_v34(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, PHASE_34_STORYFLOW_SURVEYS_SQL)


PHASE_35_STORYFLOW_RUN_METADATA_SQL = """
ALTER TABLE simulation_runs ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE simulation_runs ADD COLUMN purpose TEXT NOT NULL DEFAULT '';
ALTER TABLE simulation_runs ADD COLUMN created_by TEXT;
ALTER TABLE simulation_runs ADD COLUMN configuration JSON NOT NULL DEFAULT '{}';
ALTER TABLE simulation_runs ADD COLUMN task_id TEXT;
ALTER TABLE simulation_runs ADD COLUMN started_at TIMESTAMP;
ALTER TABLE simulation_runs ADD COLUMN paused_at TIMESTAMP;
ALTER TABLE simulation_runs ADD COLUMN completed_at TIMESTAMP;
"""


def _apply_v35(conn: sqlite3.Connection) -> None:
    _execute_sql_script(conn, PHASE_35_STORYFLOW_RUN_METADATA_SQL)


PHASE_36_STORYFLOW_EVENT_PROVENANCE_SQL = """
ALTER TABLE simulation_events ADD COLUMN source_generation_run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_simulation_events_source_generation_run
    ON simulation_events(source_generation_run_id)
    WHERE source_generation_run_id IS NOT NULL;
"""


def _apply_v36(conn: sqlite3.Connection) -> None:
    """Persist generation provenance as a first-class simulation event field."""
    _execute_sql_script(conn, PHASE_36_STORYFLOW_EVENT_PROVENANCE_SQL)


PHASE_37_STORYFLOW_SIMULATION_CLOCK_SQL = """
ALTER TABLE simulation_runs ADD COLUMN simulation_time TEXT;
"""


def _apply_v37(conn: sqlite3.Connection) -> None:
    """Persist the deterministic simulation clock at the durable run boundary."""
    _execute_sql_script(conn, PHASE_37_STORYFLOW_SIMULATION_CLOCK_SQL)


PHASE_38_STORYFLOW_SIMULATION_GRAPH_SQL = """
CREATE TABLE IF NOT EXISTS simulation_graph_projection_nodes (
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    simulation_id TEXT,
    label TEXT NOT NULL,
    payload JSON NOT NULL DEFAULT '{}',
    event_sequence INTEGER NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY(simulation_run_id, node_id)
);

CREATE TABLE IF NOT EXISTS simulation_graph_projection_edges (
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    edge_id TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    payload JSON NOT NULL DEFAULT '{}',
    event_sequence INTEGER NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY(simulation_run_id, edge_id)
);

CREATE TABLE IF NOT EXISTS simulation_graph_projection_meta (
    simulation_run_id TEXT PRIMARY KEY REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    state_hash TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    event_limit INTEGER NOT NULL,
    projection_version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_simulation_graph_nodes_run_sequence
    ON simulation_graph_projection_nodes(simulation_run_id, event_sequence);
CREATE INDEX IF NOT EXISTS idx_simulation_graph_edges_run_sequence
    ON simulation_graph_projection_edges(simulation_run_id, event_sequence);
"""


def _apply_v38(conn: sqlite3.Connection) -> None:
    """Add a rebuildable, run-scoped dynamic graph read model."""
    _execute_sql_script(conn, PHASE_38_STORYFLOW_SIMULATION_GRAPH_SQL)


PHASE_39_STORYFLOW_SCHEDULER_COST_SQL = """
-- Agent activation is an append-only, run-scoped explanation of why an Agent
-- did (or did not) receive a decision slot.  The run configuration remains
-- the durable policy source; these rows are the deterministic per-round
-- read model and audit trail.
CREATE TABLE IF NOT EXISTS simulation_agent_activations (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    round_number INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    tier TEXT NOT NULL,
    active INTEGER NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    reasons JSON NOT NULL DEFAULT '[]',
    policy JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL,
    UNIQUE(simulation_run_id, round_number, agent_id),
    CHECK(tier IN ('A', 'B', 'C')),
    CHECK(active IN (0, 1)),
    CHECK(round_number >= 1)
);

CREATE INDEX IF NOT EXISTS idx_simulation_agent_activations_run_round
    ON simulation_agent_activations(simulation_run_id, round_number, active, score DESC);

CREATE TRIGGER IF NOT EXISTS prevent_simulation_agent_activation_update
BEFORE UPDATE ON simulation_agent_activations
BEGIN
    SELECT RAISE(ABORT, 'simulation agent activations are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_agent_activation_delete
BEFORE DELETE ON simulation_agent_activations
BEGIN
    SELECT RAISE(ABORT, 'simulation agent activations are append-only');
END;

-- A cost row is keyed by the provider GenerationRun.  It lets a retry recover
-- usage from the existing model-runtime ledger without charging the same
-- generation twice, even when the worker died after the provider returned.
CREATE TABLE IF NOT EXISTS simulation_cost_ledger (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    round_number INTEGER NOT NULL,
    agent_id TEXT,
    generation_run_id TEXT NOT NULL UNIQUE REFERENCES generation_runs(id) ON DELETE RESTRICT,
    model_role TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cost_rate_per_1k REAL NOT NULL DEFAULT 0,
    estimated_cost REAL NOT NULL DEFAULT 0,
    actual_cost REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'recorded',
    created_at TIMESTAMP NOT NULL,
    CHECK(round_number >= 1),
    CHECK(prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0),
    CHECK(cost_rate_per_1k >= 0 AND estimated_cost >= 0 AND actual_cost >= 0),
    CHECK(status IN ('recorded', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_simulation_cost_ledger_run_round
    ON simulation_cost_ledger(simulation_run_id, round_number, created_at);

CREATE TRIGGER IF NOT EXISTS prevent_simulation_cost_ledger_update
BEFORE UPDATE ON simulation_cost_ledger
BEGIN
    SELECT RAISE(ABORT, 'simulation cost ledger is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_cost_ledger_delete
BEFORE DELETE ON simulation_cost_ledger
BEGIN
    SELECT RAISE(ABORT, 'simulation cost ledger is append-only');
END;
"""


def _apply_v39(conn: sqlite3.Connection) -> None:
    """Persist explainable Agent scheduling and provider usage accounting."""
    _execute_sql_script(conn, PHASE_39_STORYFLOW_SCHEDULER_COST_SQL)


PHASE_40_STORYFLOW_CAUSAL_TRACE_SQL = """
-- Causal traces are an append-only, rebuildable Sandbox ledger.  They point
-- at persisted simulation evidence and never become Canon facts.
CREATE TABLE IF NOT EXISTS simulation_causal_traces (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    event_id TEXT NOT NULL REFERENCES simulation_events(id) ON DELETE RESTRICT,
    cause_type TEXT NOT NULL,
    cause_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    evidence JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL,
    UNIQUE(event_id, cause_type, cause_id, relation),
    CHECK(cause_type IN ('prior_event', 'goal', 'memory', 'intervention',
                         'relationship', 'world_rule', 'generation'))
);

CREATE INDEX IF NOT EXISTS idx_simulation_causal_traces_run_event
    ON simulation_causal_traces(simulation_run_id, event_id, created_at);
CREATE INDEX IF NOT EXISTS idx_simulation_causal_traces_cause
    ON simulation_causal_traces(cause_type, cause_id);

CREATE TRIGGER IF NOT EXISTS prevent_simulation_causal_trace_update
BEFORE UPDATE ON simulation_causal_traces
BEGIN
    SELECT RAISE(ABORT, 'simulation causal traces are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_causal_trace_delete
BEFORE DELETE ON simulation_causal_traces
BEGIN
    SELECT RAISE(ABORT, 'simulation causal traces are append-only');
END;
"""


def _apply_v40(conn: sqlite3.Connection) -> None:
    """Persist explainable, Sandbox-only causal evidence for simulation events."""
    _execute_sql_script(conn, PHASE_40_STORYFLOW_CAUSAL_TRACE_SQL)


PHASE_41_STORYFLOW_HISTORY_SQL = """
-- Simulation history is an append-only lifecycle ledger.  Archive and
-- unarchive actions hide/show a run in the default History read model without
-- deleting its snapshot, event ledger, reports, or any Canon row.
CREATE TABLE IF NOT EXISTS simulation_run_history (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL,
    CHECK(action IN ('ARCHIVE', 'UNARCHIVE'))
);

CREATE INDEX IF NOT EXISTS idx_simulation_run_history_run_created
    ON simulation_run_history(simulation_run_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_simulation_run_history_book_created
    ON simulation_run_history(book_id, created_at DESC, id DESC);

CREATE TRIGGER IF NOT EXISTS prevent_simulation_run_history_update
BEFORE UPDATE ON simulation_run_history
BEGIN
    SELECT RAISE(ABORT, 'simulation run history is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_simulation_run_history_delete
BEFORE DELETE ON simulation_run_history
BEGIN
    SELECT RAISE(ABORT, 'simulation run history is append-only');
END;
"""


def _apply_v41(conn: sqlite3.Connection) -> None:
    """Persist archive/unarchive history without deleting Sandbox evidence."""
    _execute_sql_script(conn, PHASE_41_STORYFLOW_HISTORY_SQL)


PHASE_42_STORYFLOW_PROVENANCE_SQL = """
-- Keep fork and intervention provenance first-class and queryable.  These
-- columns describe Sandbox lineage only; they never become Canon state.
ALTER TABLE simulation_branches ADD COLUMN parent_round INTEGER;
ALTER TABLE simulation_branches ADD COLUMN fork_snapshot_hash TEXT;
ALTER TABLE simulation_interventions ADD COLUMN author TEXT NOT NULL DEFAULT 'author';
"""


def _apply_v42(conn: sqlite3.Connection) -> None:
    """Persist branch fork hashes and explicit intervention authors."""
    _execute_sql_script(conn, PHASE_42_STORYFLOW_PROVENANCE_SQL)


PHASE_43_STORYFLOW_ADOPTION_PROVENANCE_SQL = """
-- Keep the simulation-to-planning handoff queryable without requiring
-- consumers to interpret an opaque payload.  These fields remain Sandbox /
-- Planning-overlay metadata and never write Canon tables.
ALTER TABLE simulation_adoptions ADD COLUMN source_simulation_id TEXT;
ALTER TABLE simulation_adoptions ADD COLUMN source_branch_id TEXT;
ALTER TABLE simulation_adoptions ADD COLUMN source_event_range JSON NOT NULL DEFAULT '{}';
ALTER TABLE simulation_adoptions ADD COLUMN proposed_planning_nodes JSON NOT NULL DEFAULT '[]';
ALTER TABLE simulation_adoptions ADD COLUMN proposed_plot_threads JSON NOT NULL DEFAULT '[]';
ALTER TABLE simulation_adoptions ADD COLUMN proposed_character_goals JSON NOT NULL DEFAULT '[]';
ALTER TABLE simulation_adoptions ADD COLUMN proposed_foreshadows JSON NOT NULL DEFAULT '[]';
ALTER TABLE simulation_adoptions ADD COLUMN proposed_chapter_intents JSON NOT NULL DEFAULT '[]';
ALTER TABLE simulation_adoptions ADD COLUMN provenance JSON NOT NULL DEFAULT '{}';
"""


def _apply_v43(conn: sqlite3.Connection) -> None:
    """Persist structured, auditable Adoption metadata at the handoff boundary."""
    _execute_sql_script(conn, PHASE_43_STORYFLOW_ADOPTION_PROVENANCE_SQL)


PHASE_44_STORYFLOW_RUN_LINEAGE_SQL = """
-- Make Canon and branch lineage explicit on every SimulationRun.  The
-- references are descriptive Sandbox provenance; they do not authorize a
-- write to Canon.
ALTER TABLE simulation_runs ADD COLUMN base_canon_event_id TEXT;
ALTER TABLE simulation_runs ADD COLUMN branch_parent_id TEXT;
ALTER TABLE simulation_runs ADD COLUMN branch_point_event_id TEXT;

CREATE INDEX IF NOT EXISTS idx_simulation_runs_branch_parent
    ON simulation_runs(branch_parent_id)
    WHERE branch_parent_id IS NOT NULL;
"""


def _apply_v44(conn: sqlite3.Connection) -> None:
    """Persist first-class Canon and branch lineage on simulation runs."""
    _execute_sql_script(conn, PHASE_44_STORYFLOW_RUN_LINEAGE_SQL)


PHASE_45_STORYFLOW_HISTORY_DELETE_SQL = """
-- Soft deletion is an append-only History action.  Rebuild the v41 table so
-- existing databases accept DELETE while preserving every prior row.
DROP TRIGGER IF EXISTS prevent_simulation_run_history_update;
DROP TRIGGER IF EXISTS prevent_simulation_run_history_delete;
DROP INDEX IF EXISTS idx_simulation_run_history_run_created;
DROP INDEX IF EXISTS idx_simulation_run_history_book_created;
ALTER TABLE simulation_run_history RENAME TO simulation_run_history_v41;

CREATE TABLE simulation_run_history (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL,
    CHECK(action IN ('ARCHIVE', 'UNARCHIVE', 'DELETE'))
);

INSERT INTO simulation_run_history(id, simulation_run_id, book_id, action, reason, created_at)
SELECT id, simulation_run_id, book_id, action, reason, created_at
FROM simulation_run_history_v41;
DROP TABLE simulation_run_history_v41;

CREATE INDEX idx_simulation_run_history_run_created
    ON simulation_run_history(simulation_run_id, created_at DESC, id DESC);
CREATE INDEX idx_simulation_run_history_book_created
    ON simulation_run_history(book_id, created_at DESC, id DESC);

CREATE TRIGGER prevent_simulation_run_history_update
BEFORE UPDATE ON simulation_run_history
BEGIN
    SELECT RAISE(ABORT, 'simulation run history is append-only');
END;

CREATE TRIGGER prevent_simulation_run_history_delete
BEFORE DELETE ON simulation_run_history
BEGIN
    SELECT RAISE(ABORT, 'simulation run history is append-only');
END;
"""


def _apply_v45(conn: sqlite3.Connection) -> None:
    """Allow soft-delete History actions without deleting Sandbox evidence."""
    _execute_sql_script(conn, PHASE_45_STORYFLOW_HISTORY_DELETE_SQL)


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
    _Migration(15, "phase_15_per_book_style_and_plot_workspace", _apply_v15, PHASE_15_PLOT_WORKSPACE_SQL),
    _Migration(16, "phase_16_creation_workflow_and_planning_views", _apply_v16, PHASE_16_CREATION_WORKFLOW_SQL),
    _Migration(17, "phase_17_agent_extensions", _apply_v17, PHASE_17_AGENT_EXTENSIONS_SQL),
    _Migration(18, "phase_18_agent_extension_scope", _apply_v18, PHASE_18_AGENT_EXTENSION_SCOPE_SQL),
    _Migration(19, "phase_19_draft_import_analysis", _apply_v19, PHASE_19_DRAFT_IMPORT_ANALYSIS_SQL),
    _Migration(20, "phase_20_continuous_run_governance", _apply_v20, PHASE_20_CONTINUOUS_RUN_GOVERNANCE_SQL),
    _Migration(21, "phase_21_narrative_os_closure", _apply_v21, PHASE_21_NARRATIVE_OS_CLOSURE_SQL),
    _Migration(22, "phase_22_commit_rebase", _apply_v22, PHASE_22_COMMIT_REBASE_SQL),
    # Version 23 is already present in existing user databases from an earlier
    # shipped build, but its source is not in this checkout. Never reuse it.
    _Migration(24, "storyflow_simulation_foundation", _apply_v24, PHASE_24_STORYFLOW_SIMULATION_FOUNDATION_SQL),
    _Migration(25, "storyflow_simulation_ledger", _apply_v25, PHASE_25_STORYFLOW_SIMULATION_LEDGER_SQL),
    _Migration(26, "storyflow_simulation_integrity", _apply_v26, PHASE_26_STORYFLOW_SIMULATION_INTEGRITY_SQL),
    _Migration(27, "storyflow_simulation_checkpoints", _apply_v27, PHASE_27_STORYFLOW_SIMULATION_CHECKPOINT_SQL),
    _Migration(28, "storyflow_agent_memory", _apply_v28, PHASE_28_STORYFLOW_AGENT_MEMORY_SQL),
    _Migration(29, "storyflow_simulation_branches", _apply_v29, PHASE_29_STORYFLOW_SIMULATION_BRANCHES_SQL),
    _Migration(30, "storyflow_simulation_interventions", _apply_v30, PHASE_30_STORYFLOW_SIMULATION_INTERVENTIONS_SQL),
    _Migration(31, "storyflow_simulation_adoptions", _apply_v31, PHASE_31_STORYFLOW_SIMULATION_ADOPTIONS_SQL),
    _Migration(32, "storyflow_simulation_analysis", _apply_v32, PHASE_32_STORYFLOW_SIMULATION_ANALYSIS_SQL),
    _Migration(33, "storyflow_character_interactions", _apply_v33, PHASE_33_STORYFLOW_CHARACTER_INTERACTIONS_SQL),
    _Migration(34, "storyflow_surveys", _apply_v34, PHASE_34_STORYFLOW_SURVEYS_SQL),
    _Migration(35, "storyflow_run_metadata", _apply_v35, PHASE_35_STORYFLOW_RUN_METADATA_SQL),
    _Migration(36, "storyflow_event_provenance", _apply_v36, PHASE_36_STORYFLOW_EVENT_PROVENANCE_SQL),
    _Migration(37, "storyflow_simulation_clock", _apply_v37, PHASE_37_STORYFLOW_SIMULATION_CLOCK_SQL),
    _Migration(38, "storyflow_simulation_graph_projection", _apply_v38, PHASE_38_STORYFLOW_SIMULATION_GRAPH_SQL),
    _Migration(39, "storyflow_scheduler_cost_control", _apply_v39, PHASE_39_STORYFLOW_SCHEDULER_COST_SQL),
    _Migration(40, "storyflow_simulation_causal_trace", _apply_v40, PHASE_40_STORYFLOW_CAUSAL_TRACE_SQL),
    _Migration(41, "storyflow_simulation_history", _apply_v41, PHASE_41_STORYFLOW_HISTORY_SQL),
    _Migration(42, "storyflow_simulation_provenance", _apply_v42, PHASE_42_STORYFLOW_PROVENANCE_SQL),
    _Migration(43, "storyflow_adoption_provenance", _apply_v43, PHASE_43_STORYFLOW_ADOPTION_PROVENANCE_SQL),
    _Migration(44, "storyflow_run_lineage", _apply_v44, PHASE_44_STORYFLOW_RUN_LINEAGE_SQL),
    _Migration(45, "storyflow_history_soft_delete", _apply_v45, PHASE_45_STORYFLOW_HISTORY_DELETE_SQL),
)

_RUNTIME_EXTENSION_NAME = "narrative_runtime_v2"
_RUNTIME_EXTENSION_CHECKSUM = hashlib.sha256(
    f"23:{_RUNTIME_EXTENSION_NAME}:{PHASE_23_NARRATIVE_RUNTIME_V2_SQL}".encode("utf-8")
).hexdigest()


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
            conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_extensions (
                       name TEXT PRIMARY KEY,
                       version INTEGER NOT NULL,
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
            extension = conn.execute(
                "SELECT checksum FROM schema_extensions WHERE name=?",
                (_RUNTIME_EXTENSION_NAME,),
            ).fetchone()
            if extension and extension["checksum"] != _RUNTIME_EXTENSION_CHECKSUM:
                raise MigrationError("narrative runtime extension checksum mismatch")
            if extension is None:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    # A database upgraded by the previous development build
                    # may already contain v23 tables but not the extension
                    # marker.  Do not rebuild its immutable event table twice.
                    existing_tables = {
                        row["name"] for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if not {"generation_attempts", "canonical_imports", "canonical_import_items"}.issubset(existing_tables):
                        _apply_v23(conn)
                    conn.execute(
                        "INSERT INTO schema_extensions(name, version, checksum) VALUES (?, 23, ?)",
                        (_RUNTIME_EXTENSION_NAME, _RUNTIME_EXTENSION_CHECKSUM),
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
            extension_ready = False
            if "schema_extensions" in tables:
                extension_ready = source.execute(
                    "SELECT 1 FROM schema_extensions WHERE name=? AND checksum=?",
                    (_RUNTIME_EXTENSION_NAME, _RUNTIME_EXTENSION_CHECKSUM),
                ).fetchone() is not None
            if all(migration.version in applied for migration in _MIGRATIONS) and extension_ready:
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
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
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
            cursor = conn.executemany(sql, params_list)
            conn.commit()
            return cursor
    
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
    
    _IDENTIFIER_RE = __import__('re').compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

    @classmethod
    def _validate_identifier(cls, name: str, label: str = "identifier") -> None:
        """校验 SQL 标识符，防止注入"""
        if not cls._IDENTIFIER_RE.match(name):
            raise ValueError(f"Invalid SQL {label}: {name!r}")

    def insert(self, table: str, data: Dict) -> str:
        """插入记录，返回ID"""
        self._validate_identifier(table, "table name")
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
        self._validate_identifier(table, "table name")
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
        self._validate_identifier(table, "table name")
        sql = f"DELETE FROM {table} WHERE {where}"
        
        with self.connect() as conn:
            cursor = conn.execute(sql, where_params)
            conn.commit()
            return cursor.rowcount
    
    def get_by_id(self, table: str, id: str) -> Optional[Dict]:
        """根据ID获取记录"""
        self._validate_identifier(table, "table name")
        return self.fetchone(f"SELECT * FROM {table} WHERE id = ?", (id,))
    
    def list_all(self, table: str, where: str = "", params: tuple = (),
                 order_by: str = "created_at DESC", limit: int = 100) -> List[Dict]:
        """列出记录"""
        self._validate_identifier(table, "table name")
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order_by} LIMIT {limit}"
        return self.fetchall(sql, params)
    
    def count(self, table: str, where: str = "", params: tuple = ()) -> int:
        """统计记录数"""
        self._validate_identifier(table, "table name")
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
        """备份数据库（仅允许备份到 .novelforge-backups 目录）"""
        import shutil
        dest = Path(backup_path).resolve()
        backups_dir = Path(self.db_path).resolve().parent / ".novelforge-backups"
        try:
            dest.relative_to(backups_dir)
        except ValueError:
            raise ValueError(f"Backup path must be under {backups_dir}, got: {dest}")
        shutil.copy2(str(self.db_path), str(dest))
        logger.info(f"数据库备份完成: {dest}")
    
    def vacuum(self):
        """压缩数据库"""
        with self.connect() as conn:
            conn.execute("VACUUM")
        logger.info("数据库压缩完成")


# 全局数据库实例（线程安全）
_db_lock = _threading.Lock()
_db_instance: Optional[Database] = None


def get_db() -> Database:
    """获取全局数据库实例"""
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = Database()
    return _db_instance


def init_db(db_path: Optional[str] = None) -> Database:
    """初始化数据库"""
    global _db_instance
    with _db_lock:
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
