"""
NovelForge 数据库管理模块
提供数据库初始化、迁移和基础 CRUD 操作
"""

import sqlite3
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# 数据库版本
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


def generate_id() -> str:
    """生成唯一ID"""
    return str(uuid.uuid4())[:8]


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
        """初始化数据库，创建表和索引"""
        with self.connect() as conn:
            # 启用外键约束
            conn.execute("PRAGMA foreign_keys = ON")
            
            # 执行 schema
            conn.executescript(SCHEMA_SQL)
            
            # 记录版本
            cursor = conn.execute("SELECT version FROM db_version ORDER BY version DESC LIMIT 1")
            row = cursor.fetchone()
            if row is None:
                conn.execute("INSERT INTO db_version (version) VALUES (?)", (DB_VERSION,))
            
            conn.commit()
            logger.info(f"数据库初始化完成: {self.db_path}")
    
    @contextmanager
    def connect(self):
        """获取数据库连接的上下文管理器"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """执行SQL语句"""
        with self.connect() as conn:
            return conn.execute(sql, params)
    
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
        data['updated_at'] = datetime.now().isoformat()
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
