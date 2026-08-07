"""记忆系统 - 管理项目长期记忆、章节摘要和状态追踪"""

import json
import sqlite3
from pathlib import Path
from typing import Optional
from datetime import datetime
from contextlib import contextmanager

from .models import StoryProject, Foreshadowing


class MemorySystem:
    """融合 inkOS 三层记忆 + webnovel-writer RAG 概念的记忆系统"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.memory_dir = project_dir / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.memory_dir / "memory.db"
        self._init_db()

    def _init_db(self):
        """初始化SQLite记忆数据库"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chapter_summaries (
                chapter_number INTEGER PRIMARY KEY,
                summary TEXT,
                key_events TEXT,
                characters TEXT,
                locations TEXT,
                foreshadowing TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_number INTEGER,
                fact_type TEXT,
                content TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS timeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_number INTEGER,
                event TEXT,
                characters TEXT,
                location TEXT,
                timestamp TEXT
            )
        """)
        conn.commit()
        conn.close()

    @contextmanager
    def _connect(self):
        """Open a transaction connection and deterministically release its file handle."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def store_chapter_summary(self, chapter_number: int, summary: str,
                               key_events: list = None, characters: list = None,
                               locations: list = None, foreshadowing: list = None):
        """存储章节摘要"""
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO chapter_summaries
                (chapter_number, summary, key_events, characters, locations, foreshadowing, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                chapter_number,
                summary,
                json.dumps(key_events or [], ensure_ascii=False),
                json.dumps(characters or [], ensure_ascii=False),
                json.dumps(locations or [], ensure_ascii=False),
                json.dumps(foreshadowing or [], ensure_ascii=False),
                datetime.now().isoformat()
            ))

    def store_fact(self, chapter_number: int, fact_type: str, content: str):
        """存储事实"""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO facts (chapter_number, fact_type, content, created_at)
                VALUES (?, ?, ?, ?)
            """, (chapter_number, fact_type, content, datetime.now().isoformat()))

    def store_timeline_event(self, chapter_number: int, event: str,
                              characters: list = None, location: str = ""):
        """存储时间线事件"""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO timeline_events (chapter_number, event, characters, location, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (
                chapter_number, event,
                json.dumps(characters or [], ensure_ascii=False),
                location,
                datetime.now().isoformat()
            ))

    def get_recent_summaries(self, count: int = 5) -> list:
        """获取最近N章摘要"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT chapter_number, summary, key_events, characters, locations
                FROM chapter_summaries
                ORDER BY chapter_number DESC
                LIMIT ?
            """, (count,))
            results = []
            for row in cursor:
                results.append({
                    "chapter_number": row[0],
                    "summary": row[1],
                    "key_events": json.loads(row[2]) if row[2] else [],
                    "characters": json.loads(row[3]) if row[3] else [],
                    "locations": json.loads(row[4]) if row[4] else [],
                })
            return list(reversed(results))

    def get_all_summaries(self) -> list:
        """获取所有章节摘要"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT chapter_number, summary FROM chapter_summaries ORDER BY chapter_number
            """)
            results = [{"chapter_number": row[0], "summary": row[1]} for row in cursor]
            return results

    def search_facts(self, query: str, limit: int = 10) -> list:
        """搜索事实（简单关键词匹配）"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT chapter_number, fact_type, content
                FROM facts
                WHERE content LIKE ?
                ORDER BY chapter_number DESC
                LIMIT ?
            """, (f"%{query}%", limit))
            results = [{"chapter_number": row[0], "type": row[1], "content": row[2]} for row in cursor]
            return results

    def get_chapter_context(self, chapter_number: int, window: int = 3) -> str:
        """获取章节上下文（用于写作时注入）"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT chapter_number, summary, key_events, characters
                FROM chapter_summaries
                WHERE chapter_number < ?
                ORDER BY chapter_number DESC
                LIMIT ?
            """, (chapter_number, window))

            context_parts = []
            for row in cursor:
                events = json.loads(row[2]) if row[2] else []
                chars = json.loads(row[3]) if row[3] else []
                context_parts.append(
                    f"第{row[0]}章摘要: {row[1]}\n"
                    f"关键事件: {', '.join(events)}\n"
                    f"出场人物: {', '.join(chars)}"
                )

            if context_parts:
                return "【前文回顾】\n" + "\n\n".join(reversed(context_parts))
            return ""

    def get_timeline(self) -> list:
        """获取完整时间线"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT chapter_number, event, characters, location
                FROM timeline_events ORDER BY id
            """)
            results = []
            for row in cursor:
                results.append({
                    "chapter": row[0],
                    "event": row[1],
                    "characters": json.loads(row[2]) if row[2] else [],
                    "location": row[3]
                })
            return results
