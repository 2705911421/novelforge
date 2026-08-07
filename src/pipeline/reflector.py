"""Reflector - JSON delta + immutable状态更新（借鉴inkOS Reflector Agent）

核心职责：
1. 接收Observer提取的ChapterFacts
2. 生成JSON delta（增量更新，而非全量替换）
3. 校验delta结构（类似Zod schema校验）
4. 执行immutable写入（不修改原状态，返回新状态）
5. 生成状态变更日志
"""

import json
import copy
from dataclasses import dataclass, field
from typing import Optional, Any
from datetime import datetime

from .observer import ChapterFacts, CharacterFact, LocationFact, ResourceFact, \
    RelationshipFact, ForeshadowingFact, PhysicalStateFact


# ========== 校验Schema ==========

class ValidationError(Exception):
    """校验错误"""
    pass


class StateValidator:
    """状态校验器（类似Zod schema校验）"""

    # Foreshadowing status 只能是这四种
    VALID_FS_STATUS = {"open", "progressing", "resolved", "deferred"}
    # Chapter number 必须是正整数
    VALID_CHAPTER_NUM = lambda n: isinstance(n, int) and n > 0
    # 角色 status 只能是这三种
    VALID_CHAR_STATUS = {"alive", "dead", "unknown"}

    @staticmethod
    def validate_foreshadowing(data: dict) -> list:
        """校验伏笔数据，返回错误列表"""
        errors = []
        if "status" in data and data["status"] not in StateValidator.VALID_FS_STATUS:
            errors.append(f"伏笔状态无效: {data['status']}，必须是 {StateValidator.VALID_FS_STATUS}")
        if "planted_chapter" in data and not StateValidator.VALID_CHAPTER_NUM(data["planted_chapter"]):
            errors.append(f"planted_chapter 必须是正整数: {data['planted_chapter']}")
        if "resolved_chapter" in data and data["resolved_chapter"] is not None:
            if not StateValidator.VALID_CHAPTER_NUM(data["resolved_chapter"]):
                errors.append(f"resolved_chapter 必须是正整数: {data['resolved_chapter']}")
        return errors

    @staticmethod
    def validate_character(data: dict) -> list:
        """校验角色数据"""
        errors = []
        if "status" in data and data["status"] not in StateValidator.VALID_CHAR_STATUS:
            errors.append(f"角色状态无效: {data['status']}，必须是 {StateValidator.VALID_CHAR_STATUS}")
        if "name" in data and not data["name"]:
            errors.append("角色名不能为空")
        return errors

    @staticmethod
    def validate_delta(delta: dict) -> list:
        """校验整个delta"""
        errors = []
        for fs_id, fs_data in delta.get("foreshadowing", {}).get("updates", {}).items():
            errors.extend(StateValidator.validate_foreshadowing(fs_data))
        for name, char_data in delta.get("characters", {}).get("updates", {}).items():
            errors.extend(StateValidator.validate_character(char_data))
        return errors


# ========== JSON Delta 结构 ==========

@dataclass
class StateDelta:
    """状态增量更新"""
    chapter_number: int
    timestamp: str = ""
    characters: dict = field(default_factory=dict)      # {"updates": {}, "adds": []}
    locations: dict = field(default_factory=dict)        # {"updates": {}, "adds": []}
    resources: dict = field(default_factory=dict)        # {"updates": {}, "adds": []}
    relationships: dict = field(default_factory=dict)    # {"updates": {}, "adds": []}
    foreshadowing: dict = field(default_factory=dict)    # {"updates": {}, "adds": []}
    physical_states: dict = field(default_factory=dict)  # {"updates": {}}
    chapter_summary: str = ""
    key_events: list = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "chapter_number": self.chapter_number,
            "timestamp": self.timestamp,
            "characters": self.characters,
            "locations": self.locations,
            "resources": self.resources,
            "relationships": self.relationships,
            "foreshadowing": self.foreshadowing,
            "physical_states": self.physical_states,
            "chapter_summary": self.chapter_summary,
            "key_events": self.key_events,
        }

    def is_empty(self) -> bool:
        """检查delta是否为空"""
        return (not self.characters.get("updates") and not self.characters.get("adds") and
                not self.locations.get("updates") and not self.locations.get("adds") and
                not self.resources.get("updates") and not self.resources.get("adds") and
                not self.relationships.get("updates") and not self.relationships.get("adds") and
                not self.foreshadowing.get("updates") and not self.foreshadowing.get("adds") and
                not self.physical_states.get("updates"))


# ========== Reflector ==========

class Reflector:
    """Reflector Agent - JSON delta生成 + immutable状态更新

    核心原则：
    1. 输出JSON delta，而非全量markdown
    2. 代码层做Zod schema校验
    3. immutable写入（不修改原状态，返回新状态）
    4. 坏数据直接拒绝，不会滚雪球
    """

    def __init__(self):
        self.validator = StateValidator()

    def generate_delta(self, facts: ChapterFacts, current_state: dict) -> StateDelta:
        """从Observer的事实生成JSON delta

        Args:
            facts: Observer提取的章节事实
            current_state: 当前项目状态

        Returns:
            StateDelta 增量更新
        """
        delta = StateDelta(chapter_number=facts.chapter_number)

        # 1. 处理角色事实
        delta.characters = self._process_characters(facts.characters, current_state)

        # 2. 处理位置事实
        delta.locations = self._process_locations(facts.locations, current_state)

        # 3. 处理资源事实
        delta.resources = self._process_resources(facts.resources, current_state)

        # 4. 处理关系事实
        delta.relationships = self._process_relationships(facts.relationships, current_state)

        # 5. 处理伏笔事实
        delta.foreshadowing = self._process_foreshadowing(facts.foreshadowing, current_state)

        # 6. 处理物理状态
        delta.physical_states = self._process_physical_states(facts.physical_states, current_state)

        # 7. 生成章节摘要
        delta.chapter_summary = facts.get_summary()
        delta.key_events = [e for loc in facts.locations for e in loc.events]

        return delta

    def validate_and_apply(self, delta: StateDelta, current_state: dict) -> tuple:
        """校验delta并执行immutable写入

        Args:
            delta: 状态增量
            current_state: 当前状态（不会被修改）

        Returns:
            (new_state: dict, errors: list, changelog: list)

        Raises:
            ValidationError: 如果校验失败
        """
        # 校验
        errors = self.validator.validate_delta(delta.to_dict())
        if errors:
            return current_state, errors, []

        # immutable apply
        new_state = copy.deepcopy(current_state)
        changelog = []

        # 应用角色更新
        for name, updates in delta.characters.get("updates", {}).items():
            if name in new_state.get("characters", {}):
                for key, value in updates.items():
                    old_value = new_state["characters"][name].get(key)
                    new_state["characters"][name][key] = value
                    changelog.append(f"角色[{name}].{key}: {old_value} -> {value}")
        for char in delta.characters.get("adds", []):
            if "characters" not in new_state:
                new_state["characters"] = {}
            new_state["characters"][char["name"]] = char
            changelog.append(f"新增角色: {char['name']}")

        # 应用位置更新
        for name, updates in delta.locations.get("updates", {}).items():
            if name in new_state.get("locations", {}):
                for key, value in updates.items():
                    old_value = new_state["locations"][name].get(key)
                    new_state["locations"][name][key] = value
                    changelog.append(f"地点[{name}].{key}: {old_value} -> {value}")
        for loc in delta.locations.get("adds", []):
            if "locations" not in new_state:
                new_state["locations"] = {}
            new_state["locations"][loc["name"]] = loc
            changelog.append(f"新增地点: {loc['name']}")

        # 应用资源更新
        for name, updates in delta.resources.get("updates", {}).items():
            if name in new_state.get("resources", {}):
                for key, value in updates.items():
                    old_value = new_state["resources"][name].get(key)
                    new_state["resources"][name][key] = value
                    changelog.append(f"资源[{name}].{key}: {old_value} -> {value}")
        for res in delta.resources.get("adds", []):
            if "resources" not in new_state:
                new_state["resources"] = {}
            new_state["resources"][res["name"]] = res
            changelog.append(f"新增资源: {res['name']}")

        # 应用关系更新
        for rel_key, updates in delta.relationships.get("updates", {}).items():
            if rel_key in new_state.get("relationships", {}):
                for key, value in updates.items():
                    old_value = new_state["relationships"][rel_key].get(key)
                    new_state["relationships"][rel_key][key] = value
                    changelog.append(f"关系[{rel_key}].{key}: {old_value} -> {value}")
        for rel in delta.relationships.get("adds", []):
            rel_key = f"{rel['character_a']}-{rel['character_b']}"
            if "relationships" not in new_state:
                new_state["relationships"] = {}
            new_state["relationships"][rel_key] = rel
            changelog.append(f"新增关系: {rel_key}")

        # 应用伏笔更新
        for fs_id, updates in delta.foreshadowing.get("updates", {}).items():
            if fs_id in new_state.get("foreshadowing", {}):
                for key, value in updates.items():
                    old_value = new_state["foreshadowing"][fs_id].get(key)
                    new_state["foreshadowing"][fs_id][key] = value
                    changelog.append(f"伏笔[{fs_id}].{key}: {old_value} -> {value}")
        for fs in delta.foreshadowing.get("adds", []):
            fs_id = fs.get("id", f"fs_{len(new_state.get('foreshadowing', {})) + 1:03d}")
            if "foreshadowing" not in new_state:
                new_state["foreshadowing"] = {}
            new_state["foreshadowing"][fs_id] = fs
            changelog.append(f"新增伏笔: {fs_id} - {fs.get('description', '')}")

        # 应用物理状态更新
        for name, updates in delta.physical_states.get("updates", {}).items():
            if "physical_states" not in new_state:
                new_state["physical_states"] = {}
            if name not in new_state["physical_states"]:
                new_state["physical_states"][name] = {}
            for key, value in updates.items():
                old_value = new_state["physical_states"][name].get(key)
                new_state["physical_states"][name][key] = value
                changelog.append(f"状态[{name}].{key}: {old_value} -> {value}")

        return new_state, [], changelog

    def _process_characters(self, char_facts: list, current_state: dict) -> dict:
        """处理角色事实，生成delta"""
        result = {"updates": {}, "adds": []}
        known_chars = set(current_state.get("characters", {}).keys())

        for fact in char_facts:
            if fact.name in known_chars:
                # 已有角色，生成更新
                updates = {}
                if fact.state_change:
                    updates["state_change"] = fact.state_change
                if fact.emotion:
                    updates["current_emotion"] = fact.emotion
                if fact.new_info:
                    updates["new_info"] = fact.new_info
                if updates:
                    result["updates"][fact.name] = updates
            else:
                # 新角色，添加
                result["adds"].append({
                    "name": fact.name,
                    "action": fact.action,
                    "state_change": fact.state_change,
                    "emotion": fact.emotion,
                    "new_info": fact.new_info,
                    "first_appearance": current_state.get("current_chapter", 0),
                })

        return result

    def _process_locations(self, loc_facts: list, current_state: dict) -> dict:
        """处理位置事实"""
        result = {"updates": {}, "adds": []}
        known_locs = set(current_state.get("locations", {}).keys())

        for fact in loc_facts:
            if fact.name in known_locs:
                updates = {}
                if fact.description:
                    updates["description"] = fact.description
                if fact.events:
                    updates["events"] = fact.events
                if updates:
                    result["updates"][fact.name] = updates
            else:
                result["adds"].append({
                    "name": fact.name,
                    "description": fact.description,
                    "connected_from": fact.connected_from,
                    "connected_to": fact.connected_to,
                    "events": fact.events,
                })

        return result

    def _process_resources(self, res_facts: list, current_state: dict) -> dict:
        """处理资源事实"""
        result = {"updates": {}, "adds": []}
        known_res = set(current_state.get("resources", {}).keys())

        for fact in res_facts:
            if fact.name in known_res:
                updates = {}
                if fact.owner:
                    updates["owner"] = fact.owner
                if fact.action:
                    updates["last_action"] = fact.action
                if updates:
                    result["updates"][fact.name] = updates
            else:
                result["adds"].append({
                    "name": fact.name,
                    "type": fact.type,
                    "owner": fact.owner,
                    "action": fact.action,
                    "description": fact.description,
                })

        return result

    def _process_relationships(self, rel_facts: list, current_state: dict) -> dict:
        """处理关系事实"""
        result = {"updates": {}, "adds": []}
        known_rels = set(current_state.get("relationships", {}).keys())

        for fact in rel_facts:
            rel_key = f"{fact.character_a}-{fact.character_b}"
            if rel_key in known_rels:
                updates = {}
                if fact.change:
                    updates["change"] = fact.change
                if fact.reason:
                    updates["reason"] = fact.reason
                if updates:
                    result["updates"][rel_key] = updates
            else:
                result["adds"].append({
                    "character_a": fact.character_a,
                    "character_b": fact.character_b,
                    "relation_type": fact.relation_type,
                    "change": fact.change,
                    "reason": fact.reason,
                })

        return result

    def _process_foreshadowing(self, fs_facts: list, current_state: dict) -> dict:
        """处理伏笔事实"""
        result = {"updates": {}, "adds": []}
        known_fs = set(current_state.get("foreshadowing", {}).keys())
        # 使用计数器避免ID重复
        next_id = len(current_state.get("foreshadowing", {})) + 1

        for fact in fs_facts:
            if fact.id and fact.id in known_fs:
                # 已有伏笔，更新状态
                updates = {}
                if fact.action:
                    updates["status"] = fact.action
                if fact.description:
                    updates["description"] = fact.description
                if updates:
                    result["updates"][fact.id] = updates
            else:
                # 新伏笔，使用自增ID
                fs_id = fact.id or f"fs_{next_id:03d}"
                next_id += 1
                result["adds"].append({
                    "id": fs_id,
                    "description": fact.description,
                    "status": fact.action or "open",
                    "planted_chapter": fact.chapter,
                    "related_characters": fact.related_characters,
                })

        return result

    def _process_physical_states(self, ps_facts: list, current_state: dict) -> dict:
        """处理物理状态事实"""
        result = {"updates": {}}

        for fact in ps_facts:
            updates = {}
            if fact.injury:
                updates["injury"] = fact.injury
            if fact.ability_change:
                updates["ability_change"] = fact.ability_change
            if fact.physical_state:
                updates["physical_state"] = fact.physical_state
            if fact.power_level:
                updates["power_level"] = fact.power_level
            if updates:
                result["updates"][fact.character] = updates

        return result
