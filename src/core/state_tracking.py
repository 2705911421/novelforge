"""状态追踪仓库 — CHAR-004, FACTION-004, LOC-004

提供角色、势力、地点的状态追踪功能，按章节记录状态变化。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from .database import Database, generate_id, get_db
from .models import CharacterState, FactionState, LocationState


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _load(value: Optional[str], default: Any) -> Any:
    return json.loads(value) if value else default


class StateTrackingRepository:
    """状态追踪仓库

    职责：
    1. 创建/读取/更新角色状态快照
    2. 创建/读取/更新势力状态快照
    3. 创建/读取/更新地点状态快照
    4. 查询实体的状态历史
    """

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()

    # ========== 角色状态 (CHAR-004) ==========

    def create_character_state(
        self,
        character_id: str,
        chapter_id: str,
        location: str = "",
        status: str = "alive",
        relationships: Optional[dict] = None,
        knowledge: Optional[list] = None,
        emotional_state: str = "",
    ) -> str:
        """创建角色状态快照

        Args:
            character_id: 角色ID
            chapter_id: 章节ID
            location: 当前位置
            status: 状态 (alive/dead/missing/injured/captured)
            relationships: 关系变化
            knowledge: 知识/信息列表
            emotional_state: 情绪状态

        Returns:
            状态快照ID
        """
        state_id = generate_id()
        now = datetime.now().isoformat()

        self.db.execute(
            """INSERT INTO character_states(id, character_id, chapter_id, location, status,
               relationships, knowledge, emotional_state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                state_id,
                character_id,
                chapter_id,
                location,
                status,
                _json(relationships or {}),
                _json(knowledge or []),
                emotional_state,
            ),
        )

        return state_id

    def get_character_state(self, state_id: str) -> Optional[CharacterState]:
        """获取角色状态快照

        Args:
            state_id: 状态快照ID

        Returns:
            角色状态快照
        """
        row = self.db.fetchone(
            "SELECT * FROM character_states WHERE id = ?", (state_id,)
        )
        if not row:
            return None
        return self._row_to_character_state(row)

    def get_character_states(
        self,
        character_id: str,
        limit: int = 50,
    ) -> list[CharacterState]:
        """获取角色的状态历史

        Args:
            character_id: 角色ID
            limit: 返回数量限制

        Returns:
            角色状态快照列表
        """
        rows = self.db.fetchall(
            """SELECT * FROM character_states
               WHERE character_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (character_id, limit),
        )
        return [self._row_to_character_state(row) for row in rows]

    def get_character_state_at_chapter(
        self,
        character_id: str,
        chapter_id: str,
    ) -> Optional[CharacterState]:
        """获取角色在特定章节的状态

        Args:
            character_id: 角色ID
            chapter_id: 章节ID

        Returns:
            角色状态快照
        """
        row = self.db.fetchone(
            """SELECT * FROM character_states
               WHERE character_id = ? AND chapter_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (character_id, chapter_id),
        )
        if not row:
            return None
        return self._row_to_character_state(row)

    def get_latest_character_state(
        self,
        character_id: str,
    ) -> Optional[CharacterState]:
        """获取角色的最新状态

        Args:
            character_id: 角色ID

        Returns:
            角色状态快照
        """
        row = self.db.fetchone(
            """SELECT * FROM character_states
               WHERE character_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (character_id,),
        )
        if not row:
            return None
        return self._row_to_character_state(row)

    def _row_to_character_state(self, row: dict) -> CharacterState:
        """将数据库行转换为 CharacterState"""
        return CharacterState(
            character_id=row["character_id"],
            chapter_id=row["chapter_id"],
            location=row.get("location", ""),
            status=row.get("status", "alive"),
            relationships=_load(row.get("relationships"), {}),
            knowledge=_load(row.get("knowledge"), []),
            emotional_state=row.get("emotional_state", ""),
            created_at=row.get("created_at", ""),
        )

    # ========== 势力状态 (FACTION-004) ==========

    def create_faction_state(
        self,
        faction_id: str,
        chapter_id: str,
        territory: str = "",
        power_level: str = "",
        allies: Optional[list] = None,
        enemies: Optional[list] = None,
    ) -> str:
        """创建势力状态快照

        Args:
            faction_id: 势力ID
            chapter_id: 章节ID
            territory: 领地
            power_level: 力量等级
            allies: 盟友列表
            enemies: 敌人列表

        Returns:
            状态快照ID
        """
        state_id = generate_id()
        now = datetime.now().isoformat()

        self.db.execute(
            """INSERT INTO faction_states(id, faction_id, chapter_id, territory, power_level,
               allies, enemies)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                state_id,
                faction_id,
                chapter_id,
                territory,
                power_level,
                _json(allies or []),
                _json(enemies or []),
            ),
        )

        return state_id

    def get_faction_state(self, state_id: str) -> Optional[FactionState]:
        """获取势力状态快照

        Args:
            state_id: 状态快照ID

        Returns:
            势力状态快照
        """
        row = self.db.fetchone(
            "SELECT * FROM faction_states WHERE id = ?", (state_id,)
        )
        if not row:
            return None
        return self._row_to_faction_state(row)

    def get_faction_states(
        self,
        faction_id: str,
        limit: int = 50,
    ) -> list[FactionState]:
        """获取势力的状态历史

        Args:
            faction_id: 势力ID
            limit: 返回数量限制

        Returns:
            势力状态快照列表
        """
        rows = self.db.fetchall(
            """SELECT * FROM faction_states
               WHERE faction_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (faction_id, limit),
        )
        return [self._row_to_faction_state(row) for row in rows]

    def get_faction_state_at_chapter(
        self,
        faction_id: str,
        chapter_id: str,
    ) -> Optional[FactionState]:
        """获取势力在特定章节的状态

        Args:
            faction_id: 势力ID
            chapter_id: 章节ID

        Returns:
            势力状态快照
        """
        row = self.db.fetchone(
            """SELECT * FROM faction_states
               WHERE faction_id = ? AND chapter_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (faction_id, chapter_id),
        )
        if not row:
            return None
        return self._row_to_faction_state(row)

    def get_latest_faction_state(
        self,
        faction_id: str,
    ) -> Optional[FactionState]:
        """获取势力的最新状态

        Args:
            faction_id: 势力ID

        Returns:
            势力状态快照
        """
        row = self.db.fetchone(
            """SELECT * FROM faction_states
               WHERE faction_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (faction_id,),
        )
        if not row:
            return None
        return self._row_to_faction_state(row)

    def _row_to_faction_state(self, row: dict) -> FactionState:
        """将数据库行转换为 FactionState"""
        return FactionState(
            faction_id=row["faction_id"],
            chapter_id=row["chapter_id"],
            territory=row.get("territory", ""),
            power_level=row.get("power_level", ""),
            allies=_load(row.get("allies"), []),
            enemies=_load(row.get("enemies"), []),
            resources="",
            notes="",
            created_at=row.get("created_at", ""),
        )

    # ========== 地点状态 (LOC-004) ==========

    def create_location_state(
        self,
        location_id: str,
        chapter_id: str,
        controlling_faction: str = "",
        events: Optional[list] = None,
        condition: str = "",
    ) -> str:
        """创建地点状态快照

        Args:
            location_id: 地点ID
            chapter_id: 章节ID
            controlling_faction: 控制势力
            events: 事件列表
            condition: 状态/损坏情况

        Returns:
            状态快照ID
        """
        state_id = generate_id()
        now = datetime.now().isoformat()

        self.db.execute(
            """INSERT INTO location_states(id, location_id, chapter_id, controlling_faction,
               events, condition)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                state_id,
                location_id,
                chapter_id,
                controlling_faction,
                _json(events or []),
                condition,
            ),
        )

        return state_id

    def get_location_state(self, state_id: str) -> Optional[LocationState]:
        """获取地点状态快照

        Args:
            state_id: 状态快照ID

        Returns:
            地点状态快照
        """
        row = self.db.fetchone(
            "SELECT * FROM location_states WHERE id = ?", (state_id,)
        )
        if not row:
            return None
        return self._row_to_location_state(row)

    def get_location_states(
        self,
        location_id: str,
        limit: int = 50,
    ) -> list[LocationState]:
        """获取地点的状态历史

        Args:
            location_id: 地点ID
            limit: 返回数量限制

        Returns:
            地点状态快照列表
        """
        rows = self.db.fetchall(
            """SELECT * FROM location_states
               WHERE location_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (location_id, limit),
        )
        return [self._row_to_location_state(row) for row in rows]

    def get_location_state_at_chapter(
        self,
        location_id: str,
        chapter_id: str,
    ) -> Optional[LocationState]:
        """获取地点在特定章节的状态

        Args:
            location_id: 地点ID
            chapter_id: 章节ID

        Returns:
            地点状态快照
        """
        row = self.db.fetchone(
            """SELECT * FROM location_states
               WHERE location_id = ? AND chapter_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (location_id, chapter_id),
        )
        if not row:
            return None
        return self._row_to_location_state(row)

    def get_latest_location_state(
        self,
        location_id: str,
    ) -> Optional[LocationState]:
        """获取地点的最新状态

        Args:
            location_id: 地点ID

        Returns:
            地点状态快照
        """
        row = self.db.fetchone(
            """SELECT * FROM location_states
               WHERE location_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (location_id,),
        )
        if not row:
            return None
        return self._row_to_location_state(row)

    def _row_to_location_state(self, row: dict) -> LocationState:
        """将数据库行转换为 LocationState"""
        return LocationState(
            location_id=row["location_id"],
            chapter_id=row["chapter_id"],
            controlling_faction=row.get("controlling_faction", ""),
            events=_load(row.get("events"), []),
            condition=row.get("condition", ""),
            population="",
            notes="",
            created_at=row.get("created_at", ""),
        )


# 全局仓库实例
_state_tracking_repo: Optional[StateTrackingRepository] = None


def get_state_tracking_repository() -> StateTrackingRepository:
    """获取全局状态追踪仓库实例"""
    global _state_tracking_repo
    if _state_tracking_repo is None:
        _state_tracking_repo = StateTrackingRepository()
    return _state_tracking_repo
