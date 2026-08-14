"""
NovelForge 多层记忆引擎
支持三层记忆：工作记忆、情节记忆、语义记忆
"""

import json
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class MemoryLayer(str, Enum):
    """记忆层级"""
    WORKING = "working"        # 工作记忆：当前章节实时需要
    EPISODIC = "episodic"      # 情节记忆：近期章节事件
    SEMANTIC = "semantic"      # 语义记忆：长期语义事实


class MemoryCategory(str, Enum):
    """记忆类别"""
    CHARACTER_STATE = "character_state"    # 角色状态
    STORY_FACTS = "story_facts"           # 故事事实
    WORLD_RULES = "world_rules"           # 世界规则
    TIMELINE = "timeline"                 # 时间线
    OPEN_LOOPS = "open_loops"             # 伏笔/悬念
    READER_PROMISES = "reader_promises"   # 读者承诺
    RELATIONSHIPS = "relationships"       # 关系
    LOCATION_STATE = "location_state"     # 地点状态 (MEM-005)
    FACTION_STATE = "faction_state"       # 势力状态 (MEM-006)
    CHARACTER_ARC = "character_arc"       # 角色弧 (CHAR-008)
    FACTION_RELATIONSHIP = "faction_relationship"  # 势力关系 (FACTION-003)
    LOCATION_HIERARCHY = "location_hierarchy"  # 地点层级 (LOC-003)
    CHASE_DEBT = "chase_debt"  # 追读力 (REV-010)
    CHARACTER_RELATIONSHIP = "character_relationship"  # 角色关系图 (CHAR-005)
    FACTION_TIMELINE = "faction_timeline"  # 势力变化时间线 (FACTION-005)
    LOCATION_MAP = "location_map"  # 地图可视化 (LOC-005)
    AI_WRITING_ASSIST = "ai_writing_assist"  # AI写作辅助 (WRITE-004/005/006)
    PARTIAL_REVISION = "partial_revision"  # 局部修订 (REVISION-001/002)
    PARTIAL_MODIFICATION = "partial_modification"  # 局部修改 (CH-004)
    DATABASE_DIAGNOSTIC = "database_diagnostic"  # 数据库检查 (DIAG-002)
    STORY_STATE_DIAGNOSTIC = "story_state_diagnostic"  # 故事状态检查 (DIAG-004)
    RAG_DIAGNOSTIC = "rag_diagnostic"  # RAG检查 (DIAG-005)
    OPERATION_LOG = "operation_log"  # 操作日志 (DIAG-006)
    ERROR_LOG = "error_log"  # 错误日志 (DIAG-008)
    STORY_BIBLE_EXPORT = "story_bible_export"  # 故事圣经导出 (EXPORT-004)
    REVIEW_REPORT_EXPORT = "review_report_export"  # 审查报告导出 (EXPORT-005)
    FORESHADOWING_EXPORT = "foreshadowing_export"  # 伏笔表导出 (EXPORT-006)
    STREAMING_OUTPUT = "streaming_output"  # 流式输出 (WRITE-008)
    GEOGRAPHIC_MAP = "geographic_map"  # 地理地图 (WORLD-005)
    CHARACTER_CONCEPT_IMAGE = "character_concept_image"  # 角色概念图 (CHAR-007)
    CHARACTER_RELATIONSHIP_GRAPH = "character_relationship_graph"  # 角色关系图可视化 (VIS-003)
    FACTION_RELATIONSHIP_GRAPH = "faction_relationship_graph"  # 势力关系图可视化 (VIS-004)
    PLOT_STRUCTURE_GRAPH = "plot_structure_graph"  # 剧情结构图 (VIS-005)
    FORESHADOWING_GRAPH = "foreshadowing_graph"  # 伏笔图 (VIS-006)
    MAP_SYSTEM_GRAPH = "map_system_graph"  # 地图系统 (VIS-007)
    PROJECT_RESTORE = "project_restore"  # 项目恢复 (BOOK-002)
    TASK_TYPE_ADAPTATION = "task_type_adaptation"  # 任务类型适配 (CTX-005)
    AUTO_BACKUP = "auto_backup"  # 自动备份 (BACKUP-001)
    MANUAL_BACKUP = "manual_backup"  # 手动备份 (BACKUP-002)
    BACKUP_RESTORE = "backup_restore"  # 备份恢复 (BACKUP-003)
    VERSION_HISTORY = "version_history"  # 版本历史 (BACKUP-004)
    CHAPTER_EDITOR = "chapter_editor"  # 章节编辑器 (UI-003)


@dataclass
class MemoryItem:
    """记忆项"""
    id: str
    category: MemoryCategory
    content: str
    layer: MemoryLayer = MemoryLayer.SEMANTIC
    chapter_created: int = 0
    chapter_updated: int = 0
    importance: float = 1.0
    evidence: str = ""
    status: str = "active"  # active/outdated/contradicted
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "category": self.category.value,
            "content": self.content,
            "layer": self.layer.value,
            "chapter_created": self.chapter_created,
            "chapter_updated": self.chapter_updated,
            "importance": self.importance,
            "evidence": self.evidence,
            "status": self.status,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'MemoryItem':
        return cls(
            id=data["id"],
            category=MemoryCategory(data["category"]),
            content=data["content"],
            layer=MemoryLayer(data.get("layer", "semantic")),
            chapter_created=data.get("chapter_created", 0),
            chapter_updated=data.get("chapter_updated", 0),
            importance=data.get("importance", 1.0),
            evidence=data.get("evidence", ""),
            status=data.get("status", "active"),
            metadata=data.get("metadata", {}),
        )


class MemoryStore:
    """记忆存储"""

    def __init__(self):
        self.items: Dict[str, MemoryItem] = {}
        self._category_index: Dict[MemoryCategory, List[str]] = {}
        self._layer_index: Dict[MemoryLayer, List[str]] = {}
        self._on_change: callable = None

    def add(self, item: MemoryItem):
        """添加记忆项"""
        self.items[item.id] = item

        # 更新类别索引
        if item.category not in self._category_index:
            self._category_index[item.category] = []
        if item.id not in self._category_index[item.category]:
            self._category_index[item.category].append(item.id)

        # 更新层级索引
        if item.layer not in self._layer_index:
            self._layer_index[item.layer] = []
        if item.id not in self._layer_index[item.layer]:
            self._layer_index[item.layer].append(item.id)

        if self._on_change:
            self._on_change()
    
    def get(self, item_id: str) -> Optional[MemoryItem]:
        """获取记忆项"""
        return self.items.get(item_id)
    
    def update(self, item: MemoryItem):
        """更新记忆项"""
        self.items[item.id] = item
        if self._on_change:
            self._on_change()

    def remove(self, item_id: str):
        """移除记忆项"""
        if item_id in self.items:
            item = self.items[item_id]

            # 从索引中移除
            if item.category in self._category_index:
                self._category_index[item.category] = [
                    i for i in self._category_index[item.category] if i != item_id
                ]

            if item.layer in self._layer_index:
                self._layer_index[item.layer] = [
                    i for i in self._layer_index[item.layer] if i != item_id
                ]

            del self.items[item_id]
            if self._on_change:
                self._on_change()
    
    def get_by_category(self, category: MemoryCategory) -> List[MemoryItem]:
        """按类别获取记忆项"""
        item_ids = self._category_index.get(category, [])
        return [self.items[i] for i in item_ids if i in self.items]
    
    def get_by_layer(self, layer: MemoryLayer) -> List[MemoryItem]:
        """按层级获取记忆项"""
        item_ids = self._layer_index.get(layer, [])
        return [self.items[i] for i in item_ids if i in self.items]
    
    def search(self, query: str, limit: int = 10) -> List[MemoryItem]:
        """搜索记忆项"""
        query_lower = query.lower()
        results = []
        
        for item in self.items.values():
            if item.status != "active":
                continue
            
            if query_lower in item.content.lower():
                results.append(item)
        
        # 按重要性排序
        results.sort(key=lambda x: x.importance, reverse=True)
        return results[:limit]
    
    def get_active_items(self) -> List[MemoryItem]:
        """获取所有活跃记忆项"""
        return [item for item in self.items.values() if item.status == "active"]
    
    def count(self) -> int:
        """统计记忆项数量"""
        return len(self.items)
    
    def clear(self):
        """清空存储"""
        self.items.clear()
        self._category_index.clear()
        self._layer_index.clear()


class MemoryEngine:
    """记忆引擎 - 多层记忆管理"""

    def __init__(self, max_items: int = 1000, persist_path: str | None = None):
        self.store = MemoryStore()
        self.max_items = max_items
        self._counter = 0
        self._persist_path = persist_path
        self.store._on_change = self._auto_save
        if persist_path:
            self._load_from_persist_path(persist_path)

    def _generate_id(self, prefix: str = "mem") -> str:
        """生成ID（使用 UUID 避免重启后冲突）"""
        import uuid
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def _load_from_persist_path(self, path: str):
        """从持久化文件加载"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.import_from_dict(data)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _auto_save(self):
        """自动保存到持久化文件"""
        if self._persist_path:
            try:
                import os
                os.makedirs(os.path.dirname(self._persist_path) or '.', exist_ok=True)
                with open(self._persist_path, 'w', encoding='utf-8') as f:
                    json.dump(self.export_to_dict(), f, ensure_ascii=False, indent=2)
            except OSError as e:
                logger.warning("Memory auto-save failed: %s", e)
    
    def add_character_state(self, character_name: str, state: str,
                           chapter: int, evidence: str = "") -> MemoryItem:
        """添加角色状态记忆"""
        item = MemoryItem(
            id=self._generate_id("char"),
            category=MemoryCategory.CHARACTER_STATE,
            content=f"{character_name}: {state}",
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.8,
            evidence=evidence,
            metadata={"character": character_name}
        )
        self.store.add(item)
        self._check_capacity()
        return item
    
    def add_story_fact(self, fact: str, chapter: int,
                      importance: float = 1.0, evidence: str = "") -> MemoryItem:
        """添加故事事实记忆"""
        item = MemoryItem(
            id=self._generate_id("fact"),
            category=MemoryCategory.STORY_FACTS,
            content=fact,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=importance,
            evidence=evidence,
        )
        self.store.add(item)
        self._check_capacity()
        return item
    
    def add_world_rule(self, rule: str, chapter: int,
                      evidence: str = "") -> MemoryItem:
        """添加世界规则记忆"""
        item = MemoryItem(
            id=self._generate_id("rule"),
            category=MemoryCategory.WORLD_RULES,
            content=rule,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=1.0,
            evidence=evidence,
        )
        self.store.add(item)
        self._check_capacity()
        return item
    
    def add_timeline_event(self, event: str, chapter: int,
                          characters: Optional[List[str]] = None,
                          location: str = "") -> MemoryItem:
        """添加时间线事件"""
        item = MemoryItem(
            id=self._generate_id("time"),
            category=MemoryCategory.TIMELINE,
            content=event,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.7,
            metadata={
                "characters": characters or [],
                "location": location,
            }
        )
        self.store.add(item)
        self._check_capacity()
        return item
    
    def add_open_loop(self, description: str, chapter: int,
                     priority: str = "medium") -> MemoryItem:
        """添加伏笔/悬念"""
        priority_importance = {"high": 1.0, "medium": 0.7, "low": 0.4}
        
        item = MemoryItem(
            id=self._generate_id("loop"),
            category=MemoryCategory.OPEN_LOOPS,
            content=description,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=priority_importance.get(priority, 0.7),
            status="active",
            metadata={"priority": priority}
        )
        self.store.add(item)
        self._check_capacity()
        return item
    
    def resolve_loop(self, loop_id: str, chapter: int):
        """解决伏笔"""
        item = self.store.get(loop_id)
        if item:
            item.status = "resolved"
            item.chapter_updated = chapter
            self.store.update(item)

    def add_location_state(self, location_name: str, state: str,
                           chapter: int, controlling_faction: str = "",
                           condition: str = "", evidence: str = "") -> MemoryItem:
        """添加地点状态记忆 (MEM-005)

        Args:
            location_name: 地点名称
            state: 状态描述
            chapter: 章节号
            controlling_faction: 控制势力
            condition: 状态/损坏情况
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{location_name}: {state}"
        if controlling_faction:
            content += f" (控制者: {controlling_faction})"
        if condition:
            content += f" [状态: {condition}]"

        item = MemoryItem(
            id=self._generate_id("loc"),
            category=MemoryCategory.LOCATION_STATE,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.7,
            evidence=evidence,
            metadata={
                "location": location_name,
                "controlling_faction": controlling_faction,
                "condition": condition,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def add_faction_state(self, faction_name: str, state: str,
                          chapter: int, territory: str = "",
                          power_level: str = "", allies: Optional[List[str]] = None,
                          enemies: Optional[List[str]] = None,
                          evidence: str = "") -> MemoryItem:
        """添加势力状态记忆 (MEM-006)

        Args:
            faction_name: 势力名称
            state: 状态描述
            chapter: 章节号
            territory: 领地
            power_level: 力量等级
            allies: 盟友列表
            enemies: 敌人列表
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{faction_name}: {state}"
        if territory:
            content += f" (领地: {territory})"
        if power_level:
            content += f" [力量: {power_level}]"
        if allies:
            content += f" 盟友: {', '.join(allies)}"
        if enemies:
            content += f" 敌人: {', '.join(enemies)}"

        item = MemoryItem(
            id=self._generate_id("fac"),
            category=MemoryCategory.FACTION_STATE,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.7,
            evidence=evidence,
            metadata={
                "faction": faction_name,
                "territory": territory,
                "power_level": power_level,
                "allies": allies or [],
                "enemies": enemies or [],
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def add_character_arc(self, character_name: str, arc_stage: str,
                          chapter: int, goal: str = "",
                          obstacle: str = "", growth: str = "",
                          evidence: str = "") -> MemoryItem:
        """添加角色弧追踪 (CHAR-008)

        Args:
            character_name: 角色名称
            arc_stage: 弧阶段 (setup/rising/climax/resolution)
            chapter: 章节号
            goal: 角色目标
            obstacle: 面临的障碍
            growth: 成长/变化
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{character_name} [{arc_stage}]"
        if goal:
            content += f" 目标: {goal}"
        if obstacle:
            content += f" 障碍: {obstacle}"
        if growth:
            content += f" 成长: {growth}"

        item = MemoryItem(
            id=self._generate_id("arc"),
            category=MemoryCategory.CHARACTER_ARC,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.9,
            evidence=evidence,
            metadata={
                "character": character_name,
                "arc_stage": arc_stage,
                "goal": goal,
                "obstacle": obstacle,
                "growth": growth,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def add_faction_relationship(self, source_faction: str, target_faction: str,
                                  relationship_type: str, chapter: int,
                                  description: str = "",
                                  evidence: str = "") -> MemoryItem:
        """添加势力关系 (FACTION-003)

        Args:
            source_faction: 源势力
            target_faction: 目标势力
            relationship_type: 关系类型 (ally/enemy/neutral/vassal/overlord)
            chapter: 章节号
            description: 关系描述
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{source_faction} -> {target_faction}: {relationship_type}"
        if description:
            content += f" ({description})"

        item = MemoryItem(
            id=self._generate_id("frel"),
            category=MemoryCategory.FACTION_RELATIONSHIP,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.8,
            evidence=evidence,
            metadata={
                "source_faction": source_faction,
                "target_faction": target_faction,
                "relationship_type": relationship_type,
                "description": description,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_faction_relationships(self, faction_name: Optional[str] = None) -> List[MemoryItem]:
        """获取势力关系

        Args:
            faction_name: 势力名称（可选，用于过滤）

        Returns:
            势力关系记忆项列表
        """
        items = self.store.get_by_category(MemoryCategory.FACTION_RELATIONSHIP)
        if faction_name:
            items = [i for i in items if faction_name in i.content]
        return items

    def get_faction_allies(self, faction_name: str) -> List[str]:
        """获取势力的盟友

        Args:
            faction_name: 势力名称

        Returns:
            盟友势力名称列表
        """
        relationships = self.get_faction_relationships(faction_name)
        allies = []
        for rel in relationships:
            if rel.metadata.get("relationship_type") == "ally":
                source = rel.metadata.get("source_faction")
                target = rel.metadata.get("target_faction")
                if source == faction_name:
                    allies.append(target)
                else:
                    allies.append(source)
        return list(set(allies))

    def get_faction_enemies(self, faction_name: str) -> List[str]:
        """获取势力的敌人

        Args:
            faction_name: 势力名称

        Returns:
            敌对势力名称列表
        """
        relationships = self.get_faction_relationships(faction_name)
        enemies = []
        for rel in relationships:
            if rel.metadata.get("relationship_type") == "enemy":
                source = rel.metadata.get("source_faction")
                target = rel.metadata.get("target_faction")
                if source == faction_name:
                    enemies.append(target)
                else:
                    enemies.append(source)
        return list(set(enemies))

    def get_faction_relationship_graph(self) -> Dict:
        """获取势力关系图

        Returns:
            势力关系图数据
        """
        relationships = self.store.get_by_category(MemoryCategory.FACTION_RELATIONSHIP)
        graph = {"nodes": set(), "edges": []}

        for rel in relationships:
            source = rel.metadata.get("source_faction")
            target = rel.metadata.get("target_faction")
            rel_type = rel.metadata.get("relationship_type")

            graph["nodes"].add(source)
            graph["nodes"].add(target)
            graph["edges"].append({
                "source": source,
                "target": target,
                "type": rel_type,
                "description": rel.metadata.get("description", ""),
            })

        graph["nodes"] = list(graph["nodes"])
        return graph

    def add_location_hierarchy(self, location_name: str, parent_location: str,
                                location_type: str, chapter: int,
                                description: str = "",
                                evidence: str = "") -> MemoryItem:
        """添加地点层级 (LOC-003)

        Args:
            location_name: 地点名称
            parent_location: 父级地点
            location_type: 地点类型 (world/continent/country/city/region/building)
            chapter: 章节号
            description: 地点描述
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{location_name} ({location_type})"
        if parent_location:
            content += f" -> {parent_location}"
        if description:
            content += f": {description}"

        item = MemoryItem(
            id=self._generate_id("lhier"),
            category=MemoryCategory.LOCATION_HIERARCHY,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.7,
            evidence=evidence,
            metadata={
                "location_name": location_name,
                "parent_location": parent_location,
                "location_type": location_type,
                "description": description,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_location_hierarchy(self, location_name: Optional[str] = None) -> List[MemoryItem]:
        """获取地点层级

        Args:
            location_name: 地点名称（可选，用于过滤）

        Returns:
            地点层级记忆项列表
        """
        items = self.store.get_by_category(MemoryCategory.LOCATION_HIERARCHY)
        if location_name:
            items = [i for i in items if location_name in i.content]
        return items

    def get_location_children(self, parent_location: str) -> List[str]:
        """获取地点的子地点

        Args:
            parent_location: 父级地点名称

        Returns:
            子地点名称列表
        """
        items = self.store.get_by_category(MemoryCategory.LOCATION_HIERARCHY)
        children = []
        for item in items:
            if item.metadata.get("parent_location") == parent_location:
                children.append(item.metadata.get("location_name"))
        return children

    def get_location_parent(self, location_name: str) -> Optional[str]:
        """获取地点的父地点

        Args:
            location_name: 地点名称

        Returns:
            父地点名称，如果没有则返回None
        """
        items = self.store.get_by_category(MemoryCategory.LOCATION_HIERARCHY)
        for item in items:
            if item.metadata.get("location_name") == location_name:
                return item.metadata.get("parent_location")
        return None

    def get_location_tree(self, root_location: Optional[str] = None) -> Dict:
        """获取地点树

        Args:
            root_location: 根地点名称（可选）

        Returns:
            地点树数据
        """
        items = self.store.get_by_category(MemoryCategory.LOCATION_HIERARCHY)

        # 构建树结构
        tree = {}
        for item in items:
            name = item.metadata.get("location_name")
            parent = item.metadata.get("parent_location")
            loc_type = item.metadata.get("location_type")

            tree[name] = {
                "name": name,
                "type": loc_type,
                "children": [],
                "parent": parent if parent else None,
            }

        # 构建父子关系
        for name, node in tree.items():
            parent = node["parent"]
            if parent and parent in tree:
                tree[parent]["children"].append(name)

        # 如果指定了根节点，返回以该节点为根的子树
        if root_location and root_location in tree:
            return self._build_subtree(tree, root_location)

        # 否则返回所有顶级节点
        top_level = {k: v for k, v in tree.items() if v["parent"] is None or v["parent"] == ""}
        return {"locations": list(top_level.values()), "total": len(tree)}

    def _build_subtree(self, tree: Dict, location_name: str) -> Dict:
        """构建子树"""
        if location_name not in tree:
            return {}

        node = tree[location_name].copy()
        node["children"] = [
            self._build_subtree(tree, child)
            for child in tree[location_name]["children"]
        ]
        return node

    def add_chase_debt(self, description: str, chapter: int,
                       tension_level: str = "medium",
                       category: str = "plot",
                       resolved: bool = False,
                       evidence: str = "") -> MemoryItem:
        """添加追读力元素 (REV-010)

        Args:
            description: 追读力描述
            chapter: 章节号
            tension_level: 紧张程度 (low/medium/high/critical)
            category: 类别 (plot/character/mystery/conflict)
            resolved: 是否已解决
            evidence: 证据来源

        Returns:
            记忆项
        """
        tension_importance = {"low": 0.4, "medium": 0.7, "high": 1.0, "critical": 1.2}

        item = MemoryItem(
            id=self._generate_id("chase"),
            category=MemoryCategory.CHASE_DEBT,
            content=description,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=tension_importance.get(tension_level, 0.7),
            status="resolved" if resolved else "active",
            evidence=evidence,
            metadata={
                "tension_level": tension_level,
                "category": category,
                "resolved": resolved,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def resolve_chase_debt(self, chase_id: str, chapter: int):
        """解决追读力元素

        Args:
            chase_id: 追读力ID
            chapter: 章节号
        """
        item = self.store.get(chase_id)
        if item and item.category == MemoryCategory.CHASE_DEBT:
            item.status = "resolved"
            item.metadata["resolved"] = True
            item.chapter_updated = chapter
            self.store.update(item)

    def get_chase_debts(self, include_resolved: bool = False) -> List[MemoryItem]:
        """获取追读力元素

        Args:
            include_resolved: 是否包含已解决的

        Returns:
            追读力元素列表
        """
        items = self.store.get_by_category(MemoryCategory.CHASE_DEBT)
        if not include_resolved:
            items = [i for i in items if i.status == "active"]
        return items

    def get_chase_debt_score(self) -> Dict:
        """计算追读力分数

        Returns:
            追读力分数信息
        """
        active_debts = self.get_chase_debts(include_resolved=False)
        resolved_debts = self.get_chase_debts(include_resolved=True)
        resolved_debts = [i for i in resolved_debts if i.status == "resolved"]

        # 计算活跃追读力分数
        active_score = sum(d.importance for d in active_debts)

        # 按类别统计
        by_category = {}
        for debt in active_debts:
            cat = debt.metadata.get("category", "unknown")
            by_category[cat] = by_category.get(cat, 0) + 1

        # 按紧张程度统计
        by_tension = {}
        for debt in active_debts:
            tension = debt.metadata.get("tension_level", "medium")
            by_tension[tension] = by_tension.get(tension, 0) + 1

        return {
            "active_count": len(active_debts),
            "resolved_count": len(resolved_debts),
            "active_score": round(active_score, 2),
            "by_category": by_category,
            "by_tension": by_tension,
        }

    def add_character_relationship(self, source_character: str, target_character: str,
                                    relationship_type: str, chapter: int,
                                    description: str = "",
                                    evidence: str = "") -> MemoryItem:
        """添加角色关系 (CHAR-005)

        Args:
            source_character: 源角色
            target_character: 目标角色
            relationship_type: 关系类型 (friend/enemy/lover/family/mentor/rival/ally)
            chapter: 章节号
            description: 关系描述
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{source_character} -> {target_character}: {relationship_type}"
        if description:
            content += f" ({description})"

        item = MemoryItem(
            id=self._generate_id("crel"),
            category=MemoryCategory.CHARACTER_RELATIONSHIP,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.8,
            evidence=evidence,
            metadata={
                "source_character": source_character,
                "target_character": target_character,
                "relationship_type": relationship_type,
                "description": description,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_character_relationships(self, character_name: Optional[str] = None) -> List[MemoryItem]:
        """获取角色关系

        Args:
            character_name: 角色名称（可选，用于过滤）

        Returns:
            角色关系记忆项列表
        """
        items = self.store.get_by_category(MemoryCategory.CHARACTER_RELATIONSHIP)
        if character_name:
            items = [i for i in items if character_name in i.content]
        return items

    def get_character_friends(self, character_name: str) -> List[str]:
        """获取角色的朋友

        Args:
            character_name: 角色名称

        Returns:
            朋友角色名称列表
        """
        relationships = self.get_character_relationships(character_name)
        friends = []
        for rel in relationships:
            if rel.metadata.get("relationship_type") in ["friend", "ally"]:
                source = rel.metadata.get("source_character")
                target = rel.metadata.get("target_character")
                if source == character_name:
                    friends.append(target)
                else:
                    friends.append(source)
        return list(set(friends))

    def get_character_enemies(self, character_name: str) -> List[str]:
        """获取角色的敌人

        Args:
            character_name: 角色名称

        Returns:
            敌人角色名称列表
        """
        relationships = self.get_character_relationships(character_name)
        enemies = []
        for rel in relationships:
            if rel.metadata.get("relationship_type") in ["enemy", "rival"]:
                source = rel.metadata.get("source_character")
                target = rel.metadata.get("target_character")
                if source == character_name:
                    enemies.append(target)
                else:
                    enemies.append(source)
        return list(set(enemies))

    def get_character_relationship_graph(self) -> Dict:
        """获取角色关系图

        Returns:
            角色关系图数据
        """
        relationships = self.store.get_by_category(MemoryCategory.CHARACTER_RELATIONSHIP)
        graph = {"nodes": set(), "edges": []}

        for rel in relationships:
            source = rel.metadata.get("source_character")
            target = rel.metadata.get("target_character")
            rel_type = rel.metadata.get("relationship_type")

            graph["nodes"].add(source)
            graph["nodes"].add(target)
            graph["edges"].append({
                "source": source,
                "target": target,
                "type": rel_type,
                "description": rel.metadata.get("description", ""),
            })

        graph["nodes"] = list(graph["nodes"])
        return graph

    def add_faction_timeline_event(self, faction_name: str, event: str,
                                    chapter: int, event_type: str = "general",
                                    impact: str = "neutral",
                                    evidence: str = "") -> MemoryItem:
        """添加势力变化时间线事件 (FACTION-005)

        Args:
            faction_name: 势力名称
            event: 事件描述
            chapter: 章节号
            event_type: 事件类型 (rise/decline/alliance/war/territory/general)
            impact: 影响 (positive/negative/neutral)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{faction_name}: {event}"

        item = MemoryItem(
            id=self._generate_id("ftime"),
            category=MemoryCategory.FACTION_TIMELINE,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.8,
            evidence=evidence,
            metadata={
                "faction": faction_name,
                "event_type": event_type,
                "impact": impact,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_faction_timeline(self, faction_name: Optional[str] = None) -> List[MemoryItem]:
        """获取势力时间线

        Args:
            faction_name: 势力名称（可选，用于过滤）

        Returns:
            势力时间线事件列表
        """
        items = self.store.get_by_category(MemoryCategory.FACTION_TIMELINE)
        if faction_name:
            items = [i for i in items if faction_name in i.content]
        # 按章节排序
        items.sort(key=lambda x: x.chapter_created)
        return items

    def get_faction_timeline_summary(self, faction_name: str) -> Dict:
        """获取势力时间线摘要

        Args:
            faction_name: 势力名称

        Returns:
            势力时间线摘要
        """
        timeline = self.get_faction_timeline(faction_name)

        # 按事件类型统计
        by_type = {}
        for event in timeline:
            event_type = event.metadata.get("event_type", "general")
            by_type[event_type] = by_type.get(event_type, 0) + 1

        # 按影响统计
        by_impact = {}
        for event in timeline:
            impact = event.metadata.get("impact", "neutral")
            by_impact[impact] = by_impact.get(impact, 0) + 1

        return {
            "faction": faction_name,
            "total_events": len(timeline),
            "by_type": by_type,
            "by_impact": by_impact,
            "timeline": [
                {
                    "chapter": event.chapter_created,
                    "event": event.content,
                    "type": event.metadata.get("event_type", "general"),
                    "impact": event.metadata.get("impact", "neutral"),
                }
                for event in timeline
            ],
        }

    def add_location_map_point(self, location_name: str, x: float, y: float,
                                chapter: int, point_type: str = "city",
                                description: str = "",
                                evidence: str = "") -> MemoryItem:
        """添加地图点 (LOC-005)

        Args:
            location_name: 地点名称
            x: X坐标
            y: Y坐标
            chapter: 章节号
            point_type: 点类型 (city/mountain/river/fortress/temple/other)
            description: 地点描述
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{location_name} ({x}, {y})"
        if description:
            content += f": {description}"

        item = MemoryItem(
            id=self._generate_id("lmap"),
            category=MemoryCategory.LOCATION_MAP,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.7,
            evidence=evidence,
            metadata={
                "location_name": location_name,
                "x": x,
                "y": y,
                "point_type": point_type,
                "description": description,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_location_map_points(self, location_name: Optional[str] = None) -> List[MemoryItem]:
        """获取地图点

        Args:
            location_name: 地点名称（可选，用于过滤）

        Returns:
            地图点列表
        """
        items = self.store.get_by_category(MemoryCategory.LOCATION_MAP)
        if location_name:
            items = [i for i in items if location_name in i.content]
        return items

    def get_location_map_data(self) -> Dict:
        """获取地图数据

        Returns:
            地图数据
        """
        points = self.store.get_by_category(MemoryCategory.LOCATION_MAP)

        # 按类型统计
        by_type = {}
        for point in points:
            point_type = point.metadata.get("point_type", "other")
            by_type[point_type] = by_type.get(point_type, 0) + 1

        return {
            "total_points": len(points),
            "by_type": by_type,
            "points": [
                {
                    "name": point.metadata.get("location_name"),
                    "x": point.metadata.get("x"),
                    "y": point.metadata.get("y"),
                    "type": point.metadata.get("point_type"),
                    "description": point.metadata.get("description"),
                    "chapter": point.chapter_created,
                }
                for point in points
            ],
        }

    def get_location_map_bounds(self) -> Dict:
        """获取地图边界

        Returns:
            地图边界信息
        """
        points = self.store.get_by_category(MemoryCategory.LOCATION_MAP)

        if not points:
            return {"min_x": 0, "max_x": 0, "min_y": 0, "max_y": 0}

        x_values = [p.metadata.get("x", 0) for p in points]
        y_values = [p.metadata.get("y", 0) for p in points]

        return {
            "min_x": min(x_values),
            "max_x": max(x_values),
            "min_y": min(y_values),
            "max_y": max(y_values),
            "width": max(x_values) - min(x_values),
            "height": max(y_values) - min(y_values),
        }

    def add_ai_writing_assist(self, text: str, assist_type: str,
                               chapter: int, result: str = "",
                               quality_score: float = 0.0,
                               evidence: str = "") -> MemoryItem:
        """添加AI写作辅助记录 (WRITE-004/005/006)

        Args:
            text: 原始文本
            assist_type: 辅助类型 (polish/expand/condense)
            chapter: 章节号
            result: 处理结果
            quality_score: 质量分数
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{assist_type}] {text[:50]}..."
        if result:
            content += f" -> {result[:50]}..."

        item = MemoryItem(
            id=self._generate_id("aiw"),
            category=MemoryCategory.AI_WRITING_ASSIST,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.6,
            evidence=evidence,
            metadata={
                "text": text,
                "assist_type": assist_type,
                "result": result,
                "quality_score": quality_score,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_ai_writing_assists(self, assist_type: Optional[str] = None) -> List[MemoryItem]:
        """获取AI写作辅助记录

        Args:
            assist_type: 辅助类型（可选，用于过滤）

        Returns:
            AI写作辅助记录列表
        """
        items = self.store.get_by_category(MemoryCategory.AI_WRITING_ASSIST)
        if assist_type:
            items = [i for i in items if i.metadata.get("assist_type") == assist_type]
        return items

    def get_ai_writing_assist_stats(self) -> Dict:
        """获取AI写作辅助统计

        Returns:
            AI写作辅助统计信息
        """
        assists = self.store.get_by_category(MemoryCategory.AI_WRITING_ASSIST)

        # 按类型统计
        by_type = {}
        for assist in assists:
            assist_type = assist.metadata.get("assist_type", "unknown")
            by_type[assist_type] = by_type.get(assist_type, 0) + 1

        # 计算平均质量分数
        quality_scores = [a.metadata.get("quality_score", 0) for a in assists if a.metadata.get("quality_score", 0) > 0]
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0

        return {
            "total_assists": len(assists),
            "by_type": by_type,
            "average_quality": round(avg_quality, 2),
        }

    def add_partial_revision(self, original_text: str, revised_text: str,
                              chapter: int, revision_type: str = "scene",
                              start_pos: int = 0, end_pos: int = 0,
                              reason: str = "",
                              evidence: str = "") -> MemoryItem:
        """添加局部修订记录 (REVISION-001/002)

        Args:
            original_text: 原始文本
            revised_text: 修订后文本
            chapter: 章节号
            revision_type: 修订类型 (partial/scene)
            start_pos: 开始位置
            end_pos: 结束位置
            reason: 修订原因
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{revision_type}] {original_text[:30]}... -> {revised_text[:30]}..."

        item = MemoryItem(
            id=self._generate_id("rev"),
            category=MemoryCategory.PARTIAL_REVISION,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.7,
            evidence=evidence,
            metadata={
                "original_text": original_text,
                "revised_text": revised_text,
                "revision_type": revision_type,
                "start_pos": start_pos,
                "end_pos": end_pos,
                "reason": reason,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_partial_revisions(self, revision_type: Optional[str] = None) -> List[MemoryItem]:
        """获取局部修订记录

        Args:
            revision_type: 修订类型（可选，用于过滤）

        Returns:
            局部修订记录列表
        """
        items = self.store.get_by_category(MemoryCategory.PARTIAL_REVISION)
        if revision_type:
            items = [i for i in items if i.metadata.get("revision_type") == revision_type]
        return items

    def get_partial_revision_stats(self) -> Dict:
        """获取局部修订统计

        Returns:
            局部修订统计信息
        """
        revisions = self.store.get_by_category(MemoryCategory.PARTIAL_REVISION)

        # 按类型统计
        by_type = {}
        for rev in revisions:
            rev_type = rev.metadata.get("revision_type", "unknown")
            by_type[rev_type] = by_type.get(rev_type, 0) + 1

        return {
            "total_revisions": len(revisions),
            "by_type": by_type,
        }

    def add_partial_modification(self, original_text: str, modified_text: str,
                                  chapter: int, modification_type: str = "ai_polish",
                                  start_pos: int = 0, end_pos: int = 0,
                                  reason: str = "",
                                  evidence: str = "") -> MemoryItem:
        """添加局部修改记录 (CH-004)

        Args:
            original_text: 原始文本
            modified_text: 修改后文本
            chapter: 章节号
            modification_type: 修改类型 (ai_polish/ai_expand/ai_condense/manual)
            start_pos: 开始位置
            end_pos: 结束位置
            reason: 修改原因
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{modification_type}] {original_text[:30]}... -> {modified_text[:30]}..."

        item = MemoryItem(
            id=self._generate_id("mod"),
            category=MemoryCategory.PARTIAL_MODIFICATION,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.6,
            evidence=evidence,
            metadata={
                "original_text": original_text,
                "modified_text": modified_text,
                "modification_type": modification_type,
                "start_pos": start_pos,
                "end_pos": end_pos,
                "reason": reason,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_partial_modifications(self, modification_type: Optional[str] = None) -> List[MemoryItem]:
        """获取局部修改记录

        Args:
            modification_type: 修改类型（可选，用于过滤）

        Returns:
            局部修改记录列表
        """
        items = self.store.get_by_category(MemoryCategory.PARTIAL_MODIFICATION)
        if modification_type:
            items = [i for i in items if i.metadata.get("modification_type") == modification_type]
        return items

    def get_partial_modification_stats(self) -> Dict:
        """获取局部修改统计

        Returns:
            局部修改统计信息
        """
        modifications = self.store.get_by_category(MemoryCategory.PARTIAL_MODIFICATION)

        # 按类型统计
        by_type = {}
        for mod in modifications:
            mod_type = mod.metadata.get("modification_type", "unknown")
            by_type[mod_type] = by_type.get(mod_type, 0) + 1

        return {
            "total_modifications": len(modifications),
            "by_type": by_type,
        }

    def add_database_diagnostic(self, check_type: str, status: str,
                                 chapter: int, details: str = "",
                                 severity: str = "info",
                                 evidence: str = "") -> MemoryItem:
        """添加数据库检查记录 (DIAG-002)

        Args:
            check_type: 检查类型 (integrity/performance/backup/migration)
            status: 状态 (pass/warning/fail)
            chapter: 章节号
            details: 检查详情
            severity: 严重程度 (info/warning/error/critical)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{check_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("diag"),
            category=MemoryCategory.DATABASE_DIAGNOSTIC,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "check_type": check_type,
                "status": status,
                "details": details,
                "severity": severity,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_database_diagnostics(self, check_type: Optional[str] = None) -> List[MemoryItem]:
        """获取数据库检查记录

        Args:
            check_type: 检查类型（可选，用于过滤）

        Returns:
            数据库检查记录列表
        """
        items = self.store.get_by_category(MemoryCategory.DATABASE_DIAGNOSTIC)
        if check_type:
            items = [i for i in items if i.metadata.get("check_type") == check_type]
        return items

    def get_database_diagnostic_stats(self) -> Dict:
        """获取数据库检查统计

        Returns:
            数据库检查统计信息
        """
        diagnostics = self.store.get_by_category(MemoryCategory.DATABASE_DIAGNOSTIC)

        # 按类型统计
        by_type = {}
        for diag in diagnostics:
            check_type = diag.metadata.get("check_type", "unknown")
            by_type[check_type] = by_type.get(check_type, 0) + 1

        # 按状态统计
        by_status = {}
        for diag in diagnostics:
            status = diag.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 按严重程度统计
        by_severity = {}
        for diag in diagnostics:
            severity = diag.metadata.get("severity", "info")
            by_severity[severity] = by_severity.get(severity, 0) + 1

        return {
            "total_diagnostics": len(diagnostics),
            "by_type": by_type,
            "by_status": by_status,
            "by_severity": by_severity,
        }

    def add_story_state_diagnostic(self, check_type: str, status: str,
                                    chapter: int, details: str = "",
                                    severity: str = "info",
                                    evidence: str = "") -> MemoryItem:
        """添加故事状态检查记录 (DIAG-004)

        Args:
            check_type: 检查类型 (consistency/continuity/completeness/conflict)
            status: 状态 (pass/warning/fail)
            chapter: 章节号
            details: 检查详情
            severity: 严重程度 (info/warning/error/critical)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{check_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("ssd"),
            category=MemoryCategory.STORY_STATE_DIAGNOSTIC,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.6,
            evidence=evidence,
            metadata={
                "check_type": check_type,
                "status": status,
                "details": details,
                "severity": severity,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_story_state_diagnostics(self, check_type: Optional[str] = None) -> List[MemoryItem]:
        """获取故事状态检查记录

        Args:
            check_type: 检查类型（可选，用于过滤）

        Returns:
            故事状态检查记录列表
        """
        items = self.store.get_by_category(MemoryCategory.STORY_STATE_DIAGNOSTIC)
        if check_type:
            items = [i for i in items if i.metadata.get("check_type") == check_type]
        return items

    def get_story_state_diagnostic_stats(self) -> Dict:
        """获取故事状态检查统计

        Returns:
            故事状态检查统计信息
        """
        diagnostics = self.store.get_by_category(MemoryCategory.STORY_STATE_DIAGNOSTIC)

        # 按类型统计
        by_type = {}
        for diag in diagnostics:
            check_type = diag.metadata.get("check_type", "unknown")
            by_type[check_type] = by_type.get(check_type, 0) + 1

        # 按状态统计
        by_status = {}
        for diag in diagnostics:
            status = diag.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 按严重程度统计
        by_severity = {}
        for diag in diagnostics:
            severity = diag.metadata.get("severity", "info")
            by_severity[severity] = by_severity.get(severity, 0) + 1

        return {
            "total_diagnostics": len(diagnostics),
            "by_type": by_type,
            "by_status": by_status,
            "by_severity": by_severity,
        }

    def add_rag_diagnostic(self, check_type: str, status: str,
                           chapter: int, details: str = "",
                           severity: str = "info",
                           evidence: str = "") -> MemoryItem:
        """添加RAG检查记录 (DIAG-005)

        Args:
            check_type: 检查类型 (index/query/performance/coverage)
            status: 状态 (pass/warning/fail)
            chapter: 章节号
            details: 检查详情
            severity: 严重程度 (info/warning/error/critical)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{check_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("ragd"),
            category=MemoryCategory.RAG_DIAGNOSTIC,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "check_type": check_type,
                "status": status,
                "details": details,
                "severity": severity,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_rag_diagnostics(self, check_type: Optional[str] = None) -> List[MemoryItem]:
        """获取RAG检查记录

        Args:
            check_type: 检查类型（可选，用于过滤）

        Returns:
            RAG检查记录列表
        """
        items = self.store.get_by_category(MemoryCategory.RAG_DIAGNOSTIC)
        if check_type:
            items = [i for i in items if i.metadata.get("check_type") == check_type]
        return items

    def get_rag_diagnostic_stats(self) -> Dict:
        """获取RAG检查统计

        Returns:
            RAG检查统计信息
        """
        diagnostics = self.store.get_by_category(MemoryCategory.RAG_DIAGNOSTIC)

        # 按类型统计
        by_type = {}
        for diag in diagnostics:
            check_type = diag.metadata.get("check_type", "unknown")
            by_type[check_type] = by_type.get(check_type, 0) + 1

        # 按状态统计
        by_status = {}
        for diag in diagnostics:
            status = diag.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 按严重程度统计
        by_severity = {}
        for diag in diagnostics:
            severity = diag.metadata.get("severity", "info")
            by_severity[severity] = by_severity.get(severity, 0) + 1

        return {
            "total_diagnostics": len(diagnostics),
            "by_type": by_type,
            "by_status": by_status,
            "by_severity": by_severity,
        }

    def add_operation_log(self, operation_type: str, status: str,
                           chapter: int, details: str = "",
                           severity: str = "info",
                           evidence: str = "") -> MemoryItem:
        """添加操作日志记录 (DIAG-006)

        Args:
            operation_type: 操作类型 (create/update/delete/export/import/backup/restore)
            status: 状态 (success/failure/pending)
            chapter: 章节号
            details: 操作详情
            severity: 严重程度 (info/warning/error/critical)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{operation_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("olog"),
            category=MemoryCategory.OPERATION_LOG,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.4,
            evidence=evidence,
            metadata={
                "operation_type": operation_type,
                "status": status,
                "details": details,
                "severity": severity,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_operation_logs(self, operation_type: Optional[str] = None) -> List[MemoryItem]:
        """获取操作日志记录

        Args:
            operation_type: 操作类型（可选，用于过滤）

        Returns:
            操作日志记录列表
        """
        items = self.store.get_by_category(MemoryCategory.OPERATION_LOG)
        if operation_type:
            items = [i for i in items if i.metadata.get("operation_type") == operation_type]
        return items

    def get_operation_log_stats(self) -> Dict:
        """获取操作日志统计

        Returns:
            操作日志统计信息
        """
        logs = self.store.get_by_category(MemoryCategory.OPERATION_LOG)

        # 按类型统计
        by_type = {}
        for log in logs:
            op_type = log.metadata.get("operation_type", "unknown")
            by_type[op_type] = by_type.get(op_type, 0) + 1

        # 按状态统计
        by_status = {}
        for log in logs:
            status = log.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 按严重程度统计
        by_severity = {}
        for log in logs:
            severity = log.metadata.get("severity", "info")
            by_severity[severity] = by_severity.get(severity, 0) + 1

        return {
            "total_logs": len(logs),
            "by_type": by_type,
            "by_status": by_status,
            "by_severity": by_severity,
        }

    def add_error_log(self, error_type: str, message: str,
                       chapter: int, details: str = "",
                       severity: str = "error",
                       evidence: str = "") -> MemoryItem:
        """添加错误日志记录 (DIAG-008)

        Args:
            error_type: 错误类型 (api/database/file/network/validation/unknown)
            message: 错误消息
            chapter: 章节号
            details: 错误详情
            severity: 严重程度 (warning/error/critical)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{error_type}] {message[:50]}"
        if details:
            content += f": {details[:30]}"

        item = MemoryItem(
            id=self._generate_id("elog"),
            category=MemoryCategory.ERROR_LOG,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.6,
            evidence=evidence,
            metadata={
                "error_type": error_type,
                "message": message,
                "details": details,
                "severity": severity,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_error_logs(self, error_type: Optional[str] = None) -> List[MemoryItem]:
        """获取错误日志记录

        Args:
            error_type: 错误类型（可选，用于过滤）

        Returns:
            错误日志记录列表
        """
        items = self.store.get_by_category(MemoryCategory.ERROR_LOG)
        if error_type:
            items = [i for i in items if i.metadata.get("error_type") == error_type]
        return items

    def get_error_log_stats(self) -> Dict:
        """获取错误日志统计

        Returns:
            错误日志统计信息
        """
        logs = self.store.get_by_category(MemoryCategory.ERROR_LOG)

        # 按类型统计
        by_type = {}
        for log in logs:
            error_type = log.metadata.get("error_type", "unknown")
            by_type[error_type] = by_type.get(error_type, 0) + 1

        # 按严重程度统计
        by_severity = {}
        for log in logs:
            severity = log.metadata.get("severity", "error")
            by_severity[severity] = by_severity.get(severity, 0) + 1

        return {
            "total_errors": len(logs),
            "by_type": by_type,
            "by_severity": by_severity,
        }

    def add_story_bible_export(self, export_type: str, status: str,
                                chapter: int, details: str = "",
                                format: str = "json",
                                evidence: str = "") -> MemoryItem:
        """添加故事圣经导出记录 (EXPORT-004)

        Args:
            export_type: 导出类型 (full/partial/incremental)
            status: 状态 (success/failure/pending)
            chapter: 章节号
            details: 导出详情
            format: 导出格式 (json/markdown/txt/docx)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{export_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("sbe"),
            category=MemoryCategory.STORY_BIBLE_EXPORT,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "export_type": export_type,
                "status": status,
                "details": details,
                "format": format,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_story_bible_exports(self, export_type: Optional[str] = None) -> List[MemoryItem]:
        """获取故事圣经导出记录

        Args:
            export_type: 导出类型（可选，用于过滤）

        Returns:
            故事圣经导出记录列表
        """
        items = self.store.get_by_category(MemoryCategory.STORY_BIBLE_EXPORT)
        if export_type:
            items = [i for i in items if i.metadata.get("export_type") == export_type]
        return items

    def get_story_bible_export_stats(self) -> Dict:
        """获取故事圣经导出统计

        Returns:
            故事圣经导出统计信息
        """
        exports = self.store.get_by_category(MemoryCategory.STORY_BIBLE_EXPORT)

        # 按类型统计
        by_type = {}
        for export in exports:
            export_type = export.metadata.get("export_type", "unknown")
            by_type[export_type] = by_type.get(export_type, 0) + 1

        # 按状态统计
        by_status = {}
        for export in exports:
            status = export.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 按格式统计
        by_format = {}
        for export in exports:
            format_type = export.metadata.get("format", "json")
            by_format[format_type] = by_format.get(format_type, 0) + 1

        return {
            "total_exports": len(exports),
            "by_type": by_type,
            "by_status": by_status,
            "by_format": by_format,
        }

    def add_review_report_export(self, export_type: str, status: str,
                                  chapter: int, details: str = "",
                                  format: str = "json",
                                  evidence: str = "") -> MemoryItem:
        """添加审查报告导出记录 (EXPORT-005)

        Args:
            export_type: 导出类型 (full/partial/chapter)
            status: 状态 (success/failure/pending)
            chapter: 章节号
            details: 导出详情
            format: 导出格式 (json/markdown/txt/docx)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{export_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("rre"),
            category=MemoryCategory.REVIEW_REPORT_EXPORT,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "export_type": export_type,
                "status": status,
                "details": details,
                "format": format,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_review_report_exports(self, export_type: Optional[str] = None) -> List[MemoryItem]:
        """获取审查报告导出记录

        Args:
            export_type: 导出类型（可选，用于过滤）

        Returns:
            审查报告导出记录列表
        """
        items = self.store.get_by_category(MemoryCategory.REVIEW_REPORT_EXPORT)
        if export_type:
            items = [i for i in items if i.metadata.get("export_type") == export_type]
        return items

    def get_review_report_export_stats(self) -> Dict:
        """获取审查报告导出统计

        Returns:
            审查报告导出统计信息
        """
        exports = self.store.get_by_category(MemoryCategory.REVIEW_REPORT_EXPORT)

        # 按类型统计
        by_type = {}
        for export in exports:
            export_type = export.metadata.get("export_type", "unknown")
            by_type[export_type] = by_type.get(export_type, 0) + 1

        # 按状态统计
        by_status = {}
        for export in exports:
            status = export.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 按格式统计
        by_format = {}
        for export in exports:
            format_type = export.metadata.get("format", "json")
            by_format[format_type] = by_format.get(format_type, 0) + 1

        return {
            "total_exports": len(exports),
            "by_type": by_type,
            "by_status": by_status,
            "by_format": by_format,
        }

    def add_foreshadowing_export(self, export_type: str, status: str,
                                  chapter: int, details: str = "",
                                  format: str = "json",
                                  evidence: str = "") -> MemoryItem:
        """添加伏笔表导出记录 (EXPORT-006)

        Args:
            export_type: 导出类型 (full/partial/open/resolved)
            status: 状态 (success/failure/pending)
            chapter: 章节号
            details: 导出详情
            format: 导出格式 (json/markdown/txt/csv)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{export_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("fse"),
            category=MemoryCategory.FORESHADOWING_EXPORT,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "export_type": export_type,
                "status": status,
                "details": details,
                "format": format,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_foreshadowing_exports(self, export_type: Optional[str] = None) -> List[MemoryItem]:
        """获取伏笔表导出记录

        Args:
            export_type: 导出类型（可选，用于过滤）

        Returns:
            伏笔表导出记录列表
        """
        items = self.store.get_by_category(MemoryCategory.FORESHADOWING_EXPORT)
        if export_type:
            items = [i for i in items if i.metadata.get("export_type") == export_type]
        return items

    def get_foreshadowing_export_stats(self) -> Dict:
        """获取伏笔表导出统计

        Returns:
            伏笔表导出统计信息
        """
        exports = self.store.get_by_category(MemoryCategory.FORESHADOWING_EXPORT)

        # 按类型统计
        by_type = {}
        for export in exports:
            export_type = export.metadata.get("export_type", "unknown")
            by_type[export_type] = by_type.get(export_type, 0) + 1

        # 按状态统计
        by_status = {}
        for export in exports:
            status = export.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 按格式统计
        by_format = {}
        for export in exports:
            format_type = export.metadata.get("format", "json")
            by_format[format_type] = by_format.get(format_type, 0) + 1

        return {
            "total_exports": len(exports),
            "by_type": by_type,
            "by_status": by_status,
            "by_format": by_format,
        }

    def add_streaming_output(self, content: str, chapter: int,
                              status: str = "completed",
                              chunk_count: int = 1,
                              total_tokens: int = 0,
                              evidence: str = "") -> MemoryItem:
        """添加流式输出记录 (WRITE-008)

        Args:
            content: 输出内容
            chapter: 章节号
            status: 状态 (streaming/completed/interrupted)
            chunk_count: 分块数量
            total_tokens: 总token数
            evidence: 证据来源

        Returns:
            记忆项
        """
        content_preview = content[:50] + "..." if len(content) > 50 else content

        item = MemoryItem(
            id=self._generate_id("so"),
            category=MemoryCategory.STREAMING_OUTPUT,
            content=content_preview,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.4,
            evidence=evidence,
            metadata={
                "content": content,
                "status": status,
                "chunk_count": chunk_count,
                "total_tokens": total_tokens,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_streaming_outputs(self, status: Optional[str] = None) -> List[MemoryItem]:
        """获取流式输出记录

        Args:
            status: 状态（可选，用于过滤）

        Returns:
            流式输出记录列表
        """
        items = self.store.get_by_category(MemoryCategory.STREAMING_OUTPUT)
        if status:
            items = [i for i in items if i.metadata.get("status") == status]
        return items

    def get_streaming_output_stats(self) -> Dict:
        """获取流式输出统计

        Returns:
            流式输出统计信息
        """
        outputs = self.store.get_by_category(MemoryCategory.STREAMING_OUTPUT)

        # 按状态统计
        by_status = {}
        for output in outputs:
            status = output.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 计算总token数
        total_tokens = sum(o.metadata.get("total_tokens", 0) for o in outputs)

        # 计算平均分块数
        chunk_counts = [o.metadata.get("chunk_count", 1) for o in outputs]
        avg_chunks = sum(chunk_counts) / len(chunk_counts) if chunk_counts else 0

        return {
            "total_outputs": len(outputs),
            "by_status": by_status,
            "total_tokens": total_tokens,
            "average_chunks": round(avg_chunks, 2),
        }

    def add_geographic_map(self, map_name: str, map_type: str,
                            chapter: int, description: str = "",
                            width: int = 0, height: int = 0,
                            evidence: str = "") -> MemoryItem:
        """添加地理地图记录 (WORLD-005)

        Args:
            map_name: 地图名称
            map_type: 地图类型 (world/continent/country/city/dungeon)
            chapter: 章节号
            description: 地图描述
            width: 地图宽度
            height: 地图高度
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{map_name} ({map_type})"
        if description:
            content += f": {description[:30]}"

        item = MemoryItem(
            id=self._generate_id("gm"),
            category=MemoryCategory.GEOGRAPHIC_MAP,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.7,
            evidence=evidence,
            metadata={
                "map_name": map_name,
                "map_type": map_type,
                "description": description,
                "width": width,
                "height": height,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_geographic_maps(self, map_type: Optional[str] = None) -> List[MemoryItem]:
        """获取地理地图记录

        Args:
            map_type: 地图类型（可选，用于过滤）

        Returns:
            地理地图记录列表
        """
        items = self.store.get_by_category(MemoryCategory.GEOGRAPHIC_MAP)
        if map_type:
            items = [i for i in items if i.metadata.get("map_type") == map_type]
        return items

    def get_geographic_map_stats(self) -> Dict:
        """获取地理地图统计

        Returns:
            地理地图统计信息
        """
        maps = self.store.get_by_category(MemoryCategory.GEOGRAPHIC_MAP)

        # 按类型统计
        by_type = {}
        for m in maps:
            map_type = m.metadata.get("map_type", "unknown")
            by_type[map_type] = by_type.get(map_type, 0) + 1

        return {
            "total_maps": len(maps),
            "by_type": by_type,
        }

    def add_character_concept_image(self, character_name: str, image_type: str,
                                     chapter: int, description: str = "",
                                     image_url: str = "",
                                     evidence: str = "") -> MemoryItem:
        """添加角色概念图记录 (CHAR-007)

        Args:
            character_name: 角色名称
            image_type: 图片类型 (portrait/full_body/action/expressions)
            chapter: 章节号
            description: 图片描述
            image_url: 图片URL
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{character_name} ({image_type})"
        if description:
            content += f": {description[:30]}"

        item = MemoryItem(
            id=self._generate_id("cci"),
            category=MemoryCategory.CHARACTER_CONCEPT_IMAGE,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.6,
            evidence=evidence,
            metadata={
                "character_name": character_name,
                "image_type": image_type,
                "description": description,
                "image_url": image_url,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_character_concept_images(self, character_name: Optional[str] = None) -> List[MemoryItem]:
        """获取角色概念图记录

        Args:
            character_name: 角色名称（可选，用于过滤）

        Returns:
            角色概念图记录列表
        """
        items = self.store.get_by_category(MemoryCategory.CHARACTER_CONCEPT_IMAGE)
        if character_name:
            items = [i for i in items if character_name in i.content]
        return items

    def get_character_concept_image_stats(self) -> Dict:
        """获取角色概念图统计

        Returns:
            角色概念图统计信息
        """
        images = self.store.get_by_category(MemoryCategory.CHARACTER_CONCEPT_IMAGE)

        # 按类型统计
        by_type = {}
        for img in images:
            image_type = img.metadata.get("image_type", "unknown")
            by_type[image_type] = by_type.get(image_type, 0) + 1

        # 按角色统计
        by_character = {}
        for img in images:
            character = img.metadata.get("character_name", "unknown")
            by_character[character] = by_character.get(character, 0) + 1

        return {
            "total_images": len(images),
            "by_type": by_type,
            "by_character": by_character,
        }

    def add_character_relationship_graph(self, graph_name: str, chapter: int,
                                          description: str = "",
                                          graph_type: str = "static",
                                          evidence: str = "") -> MemoryItem:
        """添加角色关系图可视化记录 (VIS-003)

        Args:
            graph_name: 图表名称
            chapter: 章节号
            description: 图表描述
            graph_type: 图表类型 (static/interactive/mermaid)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{graph_name} ({graph_type})"
        if description:
            content += f": {description[:30]}"

        item = MemoryItem(
            id=self._generate_id("crg"),
            category=MemoryCategory.CHARACTER_RELATIONSHIP_GRAPH,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "graph_name": graph_name,
                "description": description,
                "graph_type": graph_type,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_character_relationship_graphs(self, graph_type: Optional[str] = None) -> List[MemoryItem]:
        """获取角色关系图可视化记录

        Args:
            graph_type: 图表类型（可选，用于过滤）

        Returns:
            角色关系图可视化记录列表
        """
        items = self.store.get_by_category(MemoryCategory.CHARACTER_RELATIONSHIP_GRAPH)
        if graph_type:
            items = [i for i in items if i.metadata.get("graph_type") == graph_type]
        return items

    def get_character_relationship_graph_stats(self) -> Dict:
        """获取角色关系图可视化统计

        Returns:
            角色关系图可视化统计信息
        """
        graphs = self.store.get_by_category(MemoryCategory.CHARACTER_RELATIONSHIP_GRAPH)

        # 按类型统计
        by_type = {}
        for g in graphs:
            graph_type = g.metadata.get("graph_type", "unknown")
            by_type[graph_type] = by_type.get(graph_type, 0) + 1

        return {
            "total_graphs": len(graphs),
            "by_type": by_type,
        }

    def add_faction_relationship_graph(self, graph_name: str, chapter: int,
                                        description: str = "",
                                        graph_type: str = "static",
                                        evidence: str = "") -> MemoryItem:
        """添加势力关系图可视化记录 (VIS-004)

        Args:
            graph_name: 图表名称
            chapter: 章节号
            description: 图表描述
            graph_type: 图表类型 (static/interactive/mermaid)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{graph_name} ({graph_type})"
        if description:
            content += f": {description[:30]}"

        item = MemoryItem(
            id=self._generate_id("frg"),
            category=MemoryCategory.FACTION_RELATIONSHIP_GRAPH,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "graph_name": graph_name,
                "description": description,
                "graph_type": graph_type,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_faction_relationship_graphs(self, graph_type: Optional[str] = None) -> List[MemoryItem]:
        """获取势力关系图可视化记录

        Args:
            graph_type: 图表类型（可选，用于过滤）

        Returns:
            势力关系图可视化记录列表
        """
        items = self.store.get_by_category(MemoryCategory.FACTION_RELATIONSHIP_GRAPH)
        if graph_type:
            items = [i for i in items if i.metadata.get("graph_type") == graph_type]
        return items

    def get_faction_relationship_graph_stats(self) -> Dict:
        """获取势力关系图可视化统计

        Returns:
            势力关系图可视化统计信息
        """
        graphs = self.store.get_by_category(MemoryCategory.FACTION_RELATIONSHIP_GRAPH)

        # 按类型统计
        by_type = {}
        for g in graphs:
            graph_type = g.metadata.get("graph_type", "unknown")
            by_type[graph_type] = by_type.get(graph_type, 0) + 1

        return {
            "total_graphs": len(graphs),
            "by_type": by_type,
        }

    def add_plot_structure_graph(self, graph_name: str, chapter: int,
                                  description: str = "",
                                  graph_type: str = "static",
                                  plot_type: str = "linear",
                                  evidence: str = "") -> MemoryItem:
        """添加剧情结构图记录 (VIS-005)

        Args:
            graph_name: 图表名称
            chapter: 章节号
            description: 图表描述
            graph_type: 图表类型 (static/interactive/mermaid)
            plot_type: 剧情类型 (linear/branching/cyclical/nonlinear)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{graph_name} ({graph_type}, {plot_type})"
        if description:
            content += f": {description[:30]}"

        item = MemoryItem(
            id=self._generate_id("psg"),
            category=MemoryCategory.PLOT_STRUCTURE_GRAPH,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "graph_name": graph_name,
                "description": description,
                "graph_type": graph_type,
                "plot_type": plot_type,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_plot_structure_graphs(self, graph_type: Optional[str] = None) -> List[MemoryItem]:
        """获取剧情结构图记录

        Args:
            graph_type: 图表类型（可选，用于过滤）

        Returns:
            剧情结构图记录列表
        """
        items = self.store.get_by_category(MemoryCategory.PLOT_STRUCTURE_GRAPH)
        if graph_type:
            items = [i for i in items if i.metadata.get("graph_type") == graph_type]
        return items

    def get_plot_structure_graph_stats(self) -> Dict:
        """获取剧情结构图统计

        Returns:
            剧情结构图统计信息
        """
        graphs = self.store.get_by_category(MemoryCategory.PLOT_STRUCTURE_GRAPH)

        # 按图表类型统计
        by_graph_type = {}
        for g in graphs:
            graph_type = g.metadata.get("graph_type", "unknown")
            by_graph_type[graph_type] = by_graph_type.get(graph_type, 0) + 1

        # 按剧情类型统计
        by_plot_type = {}
        for g in graphs:
            plot_type = g.metadata.get("plot_type", "unknown")
            by_plot_type[plot_type] = by_plot_type.get(plot_type, 0) + 1

        return {
            "total_graphs": len(graphs),
            "by_graph_type": by_graph_type,
            "by_plot_type": by_plot_type,
        }

    def add_foreshadowing_graph(self, graph_name: str, chapter: int,
                                 description: str = "",
                                 graph_type: str = "static",
                                 evidence: str = "") -> MemoryItem:
        """添加伏笔图记录 (VIS-006)

        Args:
            graph_name: 图表名称
            chapter: 章节号
            description: 图表描述
            graph_type: 图表类型 (static/interactive/mermaid)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{graph_name} ({graph_type})"
        if description:
            content += f": {description[:30]}"

        item = MemoryItem(
            id=self._generate_id("fg"),
            category=MemoryCategory.FORESHADOWING_GRAPH,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "graph_name": graph_name,
                "description": description,
                "graph_type": graph_type,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_foreshadowing_graphs(self, graph_type: Optional[str] = None) -> List[MemoryItem]:
        """获取伏笔图记录

        Args:
            graph_type: 图表类型（可选，用于过滤）

        Returns:
            伏笔图记录列表
        """
        items = self.store.get_by_category(MemoryCategory.FORESHADOWING_GRAPH)
        if graph_type:
            items = [i for i in items if i.metadata.get("graph_type") == graph_type]
        return items

    def get_foreshadowing_graph_stats(self) -> Dict:
        """获取伏笔图统计

        Returns:
            伏笔图统计信息
        """
        graphs = self.store.get_by_category(MemoryCategory.FORESHADOWING_GRAPH)

        # 按类型统计
        by_type = {}
        for g in graphs:
            graph_type = g.metadata.get("graph_type", "unknown")
            by_type[graph_type] = by_type.get(graph_type, 0) + 1

        return {
            "total_graphs": len(graphs),
            "by_type": by_type,
        }

    def add_map_system_graph(self, graph_name: str, chapter: int,
                              description: str = "",
                              graph_type: str = "static",
                              map_type: str = "world",
                              evidence: str = "") -> MemoryItem:
        """添加地图系统记录 (VIS-007)

        Args:
            graph_name: 图表名称
            chapter: 章节号
            description: 图表描述
            graph_type: 图表类型 (static/interactive/mermaid)
            map_type: 地图类型 (world/continent/country/city/dungeon)
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{graph_name} ({graph_type}, {map_type})"
        if description:
            content += f": {description[:30]}"

        item = MemoryItem(
            id=self._generate_id("msg"),
            category=MemoryCategory.MAP_SYSTEM_GRAPH,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "graph_name": graph_name,
                "description": description,
                "graph_type": graph_type,
                "map_type": map_type,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_map_system_graphs(self, graph_type: Optional[str] = None) -> List[MemoryItem]:
        """获取地图系统记录

        Args:
            graph_type: 图表类型（可选，用于过滤）

        Returns:
            地图系统记录列表
        """
        items = self.store.get_by_category(MemoryCategory.MAP_SYSTEM_GRAPH)
        if graph_type:
            items = [i for i in items if i.metadata.get("graph_type") == graph_type]
        return items

    def get_map_system_graph_stats(self) -> Dict:
        """获取地图系统统计

        Returns:
            地图系统统计信息
        """
        graphs = self.store.get_by_category(MemoryCategory.MAP_SYSTEM_GRAPH)

        # 按图表类型统计
        by_graph_type = {}
        for g in graphs:
            graph_type = g.metadata.get("graph_type", "unknown")
            by_graph_type[graph_type] = by_graph_type.get(graph_type, 0) + 1

        # 按地图类型统计
        by_map_type = {}
        for g in graphs:
            map_type = g.metadata.get("map_type", "unknown")
            by_map_type[map_type] = by_map_type.get(map_type, 0) + 1

        return {
            "total_graphs": len(graphs),
            "by_graph_type": by_graph_type,
            "by_map_type": by_map_type,
        }

    def add_project_restore(self, restore_type: str, status: str,
                             chapter: int, details: str = "",
                             backup_version: str = "",
                             evidence: str = "") -> MemoryItem:
        """添加项目恢复记录 (BOOK-002)

        Args:
            restore_type: 恢复类型 (full/partial/selective)
            status: 状态 (success/failure/pending)
            chapter: 章节号
            details: 恢复详情
            backup_version: 备份版本
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{restore_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("pr"),
            category=MemoryCategory.PROJECT_RESTORE,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.6,
            evidence=evidence,
            metadata={
                "restore_type": restore_type,
                "status": status,
                "details": details,
                "backup_version": backup_version,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_project_restores(self, restore_type: Optional[str] = None) -> List[MemoryItem]:
        """获取项目恢复记录

        Args:
            restore_type: 恢复类型（可选，用于过滤）

        Returns:
            项目恢复记录列表
        """
        items = self.store.get_by_category(MemoryCategory.PROJECT_RESTORE)
        if restore_type:
            items = [i for i in items if i.metadata.get("restore_type") == restore_type]
        return items

    def get_project_restore_stats(self) -> Dict:
        """获取项目恢复统计

        Returns:
            项目恢复统计信息
        """
        restores = self.store.get_by_category(MemoryCategory.PROJECT_RESTORE)

        # 按类型统计
        by_type = {}
        for restore in restores:
            restore_type = restore.metadata.get("restore_type", "unknown")
            by_type[restore_type] = by_type.get(restore_type, 0) + 1

        # 按状态统计
        by_status = {}
        for restore in restores:
            status = restore.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        return {
            "total_restores": len(restores),
            "by_type": by_type,
            "by_status": by_status,
        }

    def add_task_type_adaptation(self, task_type: str, adaptation_type: str,
                                  chapter: int, details: str = "",
                                  evidence: str = "") -> MemoryItem:
        """添加任务类型适配记录 (CTX-005)

        Args:
            task_type: 任务类型 (write/review/query/export)
            adaptation_type: 适配类型 (context_length/prompt_style/output_format)
            chapter: 章节号
            details: 适配详情
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{task_type}] {adaptation_type}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("tta"),
            category=MemoryCategory.TASK_TYPE_ADAPTATION,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "task_type": task_type,
                "adaptation_type": adaptation_type,
                "details": details,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_task_type_adaptations(self, task_type: Optional[str] = None) -> List[MemoryItem]:
        """获取任务类型适配记录

        Args:
            task_type: 任务类型（可选，用于过滤）

        Returns:
            任务类型适配记录列表
        """
        items = self.store.get_by_category(MemoryCategory.TASK_TYPE_ADAPTATION)
        if task_type:
            items = [i for i in items if i.metadata.get("task_type") == task_type]
        return items

    def get_task_type_adaptation_stats(self) -> Dict:
        """获取任务类型适配统计

        Returns:
            任务类型适配统计信息
        """
        adaptations = self.store.get_by_category(MemoryCategory.TASK_TYPE_ADAPTATION)

        # 按任务类型统计
        by_task_type = {}
        for adapt in adaptations:
            task_type = adapt.metadata.get("task_type", "unknown")
            by_task_type[task_type] = by_task_type.get(task_type, 0) + 1

        # 按适配类型统计
        by_adaptation_type = {}
        for adapt in adaptations:
            adaptation_type = adapt.metadata.get("adaptation_type", "unknown")
            by_adaptation_type[adaptation_type] = by_adaptation_type.get(adaptation_type, 0) + 1

        return {
            "total_adaptations": len(adaptations),
            "by_task_type": by_task_type,
            "by_adaptation_type": by_adaptation_type,
        }

    def add_auto_backup(self, backup_type: str, status: str,
                         chapter: int, details: str = "",
                         backup_size: int = 0,
                         evidence: str = "") -> MemoryItem:
        """添加自动备份记录 (BACKUP-001)

        Args:
            backup_type: 备份类型 (chapter_commit/project_save/daily)
            status: 状态 (success/failure/pending)
            chapter: 章节号
            details: 备份详情
            backup_size: 备份大小（字节）
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{backup_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("ab"),
            category=MemoryCategory.AUTO_BACKUP,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.4,
            evidence=evidence,
            metadata={
                "backup_type": backup_type,
                "status": status,
                "details": details,
                "backup_size": backup_size,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_auto_backups(self, backup_type: Optional[str] = None) -> List[MemoryItem]:
        """获取自动备份记录

        Args:
            backup_type: 备份类型（可选，用于过滤）

        Returns:
            自动备份记录列表
        """
        items = self.store.get_by_category(MemoryCategory.AUTO_BACKUP)
        if backup_type:
            items = [i for i in items if i.metadata.get("backup_type") == backup_type]
        return items

    def get_auto_backup_stats(self) -> Dict:
        """获取自动备份统计

        Returns:
            自动备份统计信息
        """
        backups = self.store.get_by_category(MemoryCategory.AUTO_BACKUP)

        # 按类型统计
        by_type = {}
        for backup in backups:
            backup_type = backup.metadata.get("backup_type", "unknown")
            by_type[backup_type] = by_type.get(backup_type, 0) + 1

        # 按状态统计
        by_status = {}
        for backup in backups:
            status = backup.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 计算总备份大小
        total_size = sum(b.metadata.get("backup_size", 0) for b in backups)

        return {
            "total_backups": len(backups),
            "by_type": by_type,
            "by_status": by_status,
            "total_size": total_size,
        }

    def add_manual_backup(self, backup_name: str, status: str,
                           chapter: int, details: str = "",
                           backup_size: int = 0,
                           evidence: str = "") -> MemoryItem:
        """添加手动备份记录 (BACKUP-002)

        Args:
            backup_name: 备份名称
            status: 状态 (success/failure/pending)
            chapter: 章节号
            details: 备份详情
            backup_size: 备份大小（字节）
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"{backup_name} ({status})"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("mb"),
            category=MemoryCategory.MANUAL_BACKUP,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "backup_name": backup_name,
                "status": status,
                "details": details,
                "backup_size": backup_size,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_manual_backups(self, status: Optional[str] = None) -> List[MemoryItem]:
        """获取手动备份记录

        Args:
            status: 状态（可选，用于过滤）

        Returns:
            手动备份记录列表
        """
        items = self.store.get_by_category(MemoryCategory.MANUAL_BACKUP)
        if status:
            items = [i for i in items if i.metadata.get("status") == status]
        return items

    def get_manual_backup_stats(self) -> Dict:
        """获取手动备份统计

        Returns:
            手动备份统计信息
        """
        backups = self.store.get_by_category(MemoryCategory.MANUAL_BACKUP)

        # 按状态统计
        by_status = {}
        for backup in backups:
            status = backup.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        # 计算总备份大小
        total_size = sum(b.metadata.get("backup_size", 0) for b in backups)

        return {
            "total_backups": len(backups),
            "by_status": by_status,
            "total_size": total_size,
        }

    def add_backup_restore(self, restore_type: str, status: str,
                            chapter: int, details: str = "",
                            backup_version: str = "",
                            evidence: str = "") -> MemoryItem:
        """添加备份恢复记录 (BACKUP-003)

        Args:
            restore_type: 恢复类型 (full/partial/selective)
            status: 状态 (success/failure/pending)
            chapter: 章节号
            details: 恢复详情
            backup_version: 备份版本
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{restore_type}] {status}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("brestore"),
            category=MemoryCategory.BACKUP_RESTORE,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.6,
            evidence=evidence,
            metadata={
                "restore_type": restore_type,
                "status": status,
                "details": details,
                "backup_version": backup_version,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_backup_restores(self, restore_type: Optional[str] = None) -> List[MemoryItem]:
        """获取备份恢复记录

        Args:
            restore_type: 恢复类型（可选，用于过滤）

        Returns:
            备份恢复记录列表
        """
        items = self.store.get_by_category(MemoryCategory.BACKUP_RESTORE)
        if restore_type:
            items = [i for i in items if i.metadata.get("restore_type") == restore_type]
        return items

    def get_backup_restore_stats(self) -> Dict:
        """获取备份恢复统计

        Returns:
            备份恢复统计信息
        """
        restores = self.store.get_by_category(MemoryCategory.BACKUP_RESTORE)

        # 按类型统计
        by_type = {}
        for restore in restores:
            restore_type = restore.metadata.get("restore_type", "unknown")
            by_type[restore_type] = by_type.get(restore_type, 0) + 1

        # 按状态统计
        by_status = {}
        for restore in restores:
            status = restore.metadata.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

        return {
            "total_restores": len(restores),
            "by_type": by_type,
            "by_status": by_status,
        }

    def add_version_history(self, version: str, change_type: str,
                             chapter: int, description: str = "",
                             author: str = "system",
                             evidence: str = "") -> MemoryItem:
        """添加版本历史记录 (BACKUP-004)

        Args:
            version: 版本号
            change_type: 变更类型 (major/minor/patch/hotfix)
            chapter: 章节号
            description: 变更描述
            author: 作者
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"v{version} ({change_type})"
        if description:
            content += f": {description[:50]}"

        item = MemoryItem(
            id=self._generate_id("vh"),
            category=MemoryCategory.VERSION_HISTORY,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.5,
            evidence=evidence,
            metadata={
                "version": version,
                "change_type": change_type,
                "description": description,
                "author": author,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_version_history(self, change_type: Optional[str] = None) -> List[MemoryItem]:
        """获取版本历史记录

        Args:
            change_type: 变更类型（可选，用于过滤）

        Returns:
            版本历史记录列表
        """
        items = self.store.get_by_category(MemoryCategory.VERSION_HISTORY)
        if change_type:
            items = [i for i in items if i.metadata.get("change_type") == change_type]
        return items

    def get_version_history_stats(self) -> Dict:
        """获取版本历史统计

        Returns:
            版本历史统计信息
        """
        history = self.store.get_by_category(MemoryCategory.VERSION_HISTORY)

        # 按变更类型统计
        by_change_type = {}
        for h in history:
            change_type = h.metadata.get("change_type", "unknown")
            by_change_type[change_type] = by_change_type.get(change_type, 0) + 1

        # 按作者统计
        by_author = {}
        for h in history:
            author = h.metadata.get("author", "unknown")
            by_author[author] = by_author.get(author, 0) + 1

        return {
            "total_versions": len(history),
            "by_change_type": by_change_type,
            "by_author": by_author,
        }

    def add_chapter_editor_session(self, chapter_id: str, editor_type: str,
                                    chapter: int, details: str = "",
                                    word_count: int = 0,
                                    evidence: str = "") -> MemoryItem:
        """添加章节编辑器会话记录 (UI-003)

        Args:
            chapter_id: 章节ID
            editor_type: 编辑器类型 (rich_text/markdown/plain)
            chapter: 章节号
            details: 编辑详情
            word_count: 字数统计
            evidence: 证据来源

        Returns:
            记忆项
        """
        content = f"[{editor_type}] Chapter {chapter_id}"
        if details:
            content += f": {details[:50]}"

        item = MemoryItem(
            id=self._generate_id("ce"),
            category=MemoryCategory.CHAPTER_EDITOR,
            content=content,
            layer=MemoryLayer.EPISODIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.4,
            evidence=evidence,
            metadata={
                "chapter_id": chapter_id,
                "editor_type": editor_type,
                "details": details,
                "word_count": word_count,
            },
        )
        self.store.add(item)
        self._check_capacity()
        return item

    def get_chapter_editor_sessions(self, editor_type: Optional[str] = None) -> List[MemoryItem]:
        """获取章节编辑器会话记录

        Args:
            editor_type: 编辑器类型（可选，用于过滤）

        Returns:
            章节编辑器会话记录列表
        """
        items = self.store.get_by_category(MemoryCategory.CHAPTER_EDITOR)
        if editor_type:
            items = [i for i in items if i.metadata.get("editor_type") == editor_type]
        return items

    def get_chapter_editor_stats(self) -> Dict:
        """获取章节编辑器统计

        Returns:
            章节编辑器统计信息
        """
        sessions = self.store.get_by_category(MemoryCategory.CHAPTER_EDITOR)

        # 按编辑器类型统计
        by_editor_type = {}
        for s in sessions:
            editor_type = s.metadata.get("editor_type", "unknown")
            by_editor_type[editor_type] = by_editor_type.get(editor_type, 0) + 1

        # 计算总字数
        total_words = sum(s.metadata.get("word_count", 0) for s in sessions)

        return {
            "total_sessions": len(sessions),
            "by_editor_type": by_editor_type,
            "total_words": total_words,
        }

    def get_character_arcs(self, character_name: Optional[str] = None) -> List[MemoryItem]:
        """获取角色弧信息

        Args:
            character_name: 角色名称（可选，用于过滤）

        Returns:
            角色弧记忆项列表
        """
        items = self.store.get_by_category(MemoryCategory.CHARACTER_ARC)
        if character_name:
            items = [i for i in items if character_name in i.content]
        return items

    def get_character_arc_progress(self, character_name: str) -> Dict:
        """获取角色弧进度

        Args:
            character_name: 角色名称

        Returns:
            角色弧进度信息
        """
        arcs = self.get_character_arcs(character_name)
        if not arcs:
            return {"character": character_name, "stages": [], "current_stage": None, "total_progress": 0}

        stages = []
        for arc in arcs:
            stage = arc.metadata.get("arc_stage", "unknown")
            stages.append({
                "stage": stage,
                "chapter": arc.chapter_created,
                "goal": arc.metadata.get("goal", ""),
                "obstacle": arc.metadata.get("obstacle", ""),
                "growth": arc.metadata.get("growth", ""),
            })

        return {
            "character": character_name,
            "stages": stages,
            "current_stage": stages[-1]["stage"] if stages else None,
            "total_progress": len(stages),
        }

    def add_relationship(self, source: str, target: str,
                        relationship: str, chapter: int) -> MemoryItem:
        """添加关系记忆"""
        item = MemoryItem(
            id=self._generate_id("rel"),
            category=MemoryCategory.RELATIONSHIPS,
            content=f"{source} -> {target}: {relationship}",
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.8,
            metadata={
                "source": source,
                "target": target,
                "relationship": relationship,
            }
        )
        self.store.add(item)
        self._check_capacity()
        return item
    
    def add_reader_promise(self, promise: str, chapter: int) -> MemoryItem:
        """添加读者承诺"""
        item = MemoryItem(
            id=self._generate_id("promise"),
            category=MemoryCategory.READER_PROMISES,
            content=promise,
            layer=MemoryLayer.SEMANTIC,
            chapter_created=chapter,
            chapter_updated=chapter,
            importance=0.9,
        )
        self.store.add(item)
        self._check_capacity()
        return item
    
    def search(self, query: str, limit: int = 10) -> List[MemoryItem]:
        """搜索记忆"""
        return self.store.search(query, limit)
    
    def get_character_states(self, character_name: Optional[str] = None) -> List[MemoryItem]:
        """获取角色状态"""
        items = self.store.get_by_category(MemoryCategory.CHARACTER_STATE)
        if character_name:
            items = [i for i in items if character_name in i.content]
        return items
    
    def get_open_loops(self) -> List[MemoryItem]:
        """获取未解决的伏笔"""
        items = self.store.get_by_category(MemoryCategory.OPEN_LOOPS)
        return [i for i in items if i.status == "active"]
    
    def get_recent_events(self, count: int = 10) -> List[MemoryItem]:
        """获取最近的事件"""
        items = self.store.get_by_category(MemoryCategory.TIMELINE)
        items.sort(key=lambda x: x.chapter_created, reverse=True)
        return items[:count]
    
    def get_world_rules(self) -> List[MemoryItem]:
        """获取世界规则"""
        return self.store.get_by_category(MemoryCategory.WORLD_RULES)
    
    def get_relationships(self) -> List[MemoryItem]:
        """获取关系"""
        return self.store.get_by_category(MemoryCategory.RELATIONSHIPS)
    
    def build_context(self, chapter: int, max_tokens: int = 2000,
                     focus_characters: Optional[List[str]] = None) -> str:
        """
        构建记忆上下文
        
        Args:
            chapter: 当前章节号
            max_tokens: 最大token数
            focus_characters: 聚焦角色
            
        Returns:
            格式化的上下文文本
        """
        context_parts = []
        current_tokens = 0
        
        # 1. 世界规则 (高优先级)
        rules = self.get_world_rules()
        if rules:
            rules_text = "【世界规则】\n"
            for rule in rules[:5]:
                rules_text += f"- {rule.content}\n"
            context_parts.append(rules_text)
            current_tokens += len(rules_text) // 2
        
        # 2. 角色状态
        if focus_characters:
            for char in focus_characters:
                states = self.get_character_states(char)
                if states:
                    latest = states[-1]
                    char_text = f"【{char}状态】\n{latest.content}\n"
                    if current_tokens + len(char_text) // 2 < max_tokens:
                        context_parts.append(char_text)
                        current_tokens += len(char_text) // 2

        # 2.5 地点状态 (MEM-005)
        location_states = self.store.get_by_category(MemoryCategory.LOCATION_STATE)
        if location_states:
            # 获取最新的地点状态
            recent_locations = sorted(location_states, key=lambda x: x.chapter_created, reverse=True)[:3]
            if recent_locations:
                loc_text = "【地点状态】\n"
                for loc in recent_locations:
                    loc_text += f"- {loc.content}\n"
                if current_tokens + len(loc_text) // 2 < max_tokens:
                    context_parts.append(loc_text)
                    current_tokens += len(loc_text) // 2

        # 2.6 势力状态 (MEM-006)
        faction_states = self.store.get_by_category(MemoryCategory.FACTION_STATE)
        if faction_states:
            # 获取最新的势力状态
            recent_factions = sorted(faction_states, key=lambda x: x.chapter_created, reverse=True)[:3]
            if recent_factions:
                fac_text = "【势力状态】\n"
                for fac in recent_factions:
                    fac_text += f"- {fac.content}\n"
                if current_tokens + len(fac_text) // 2 < max_tokens:
                    context_parts.append(fac_text)
                    current_tokens += len(fac_text) // 2

        # 2.7 角色弧 (CHAR-008)
        if focus_characters:
            arc_text = "【角色弧】\n"
            for char in focus_characters:
                arcs = self.get_character_arcs(char)
                if arcs:
                    latest = arcs[-1]
                    arc_text += f"- {latest.content}\n"
            if arc_text != "【角色弧】\n":
                if current_tokens + len(arc_text) // 2 < max_tokens:
                    context_parts.append(arc_text)
                    current_tokens += len(arc_text) // 2

        # 3. 开放伏笔
        loops = self.get_open_loops()
        if loops:
            loops_text = "【未解决伏笔】\n"
            for loop in loops[:5]:
                loops_text += f"- {loop.content}\n"
            if current_tokens + len(loops_text) // 2 < max_tokens:
                context_parts.append(loops_text)
                current_tokens += len(loops_text) // 2
        
        # 4. 最近事件
        events = self.get_recent_events(5)
        if events:
            events_text = "【近期事件】\n"
            for event in events:
                events_text += f"- 第{event.chapter_created}章: {event.content}\n"
            if current_tokens + len(events_text) // 2 < max_tokens:
                context_parts.append(events_text)
                current_tokens += len(events_text) // 2
        
        return "\n".join(context_parts)
    
    def _check_capacity(self):
        """检查容量，必要时压缩"""
        if self.store.count() > self.max_items:
            self._compress()
    
    def _compress(self):
        """压缩记忆 - 移除低重要性的旧记忆"""
        items = self.store.get_active_items()
        
        # 按重要性和时间排序
        items.sort(key=lambda x: (x.importance, x.chapter_updated))
        
        # 移除最不重要的项
        remove_count = self.store.count() - self.max_items + 100
        for item in items[:remove_count]:
            item.status = "outdated"
            self.store.update(item)
        
        logger.info(f"记忆压缩完成，移除 {remove_count} 项")
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        stats = {
            "total_items": self.store.count(),
            "by_category": {},
            "by_layer": {},
            "active_loops": len(self.get_open_loops()),
        }
        
        for category in MemoryCategory:
            items = self.store.get_by_category(category)
            stats["by_category"][category.value] = len(items)
        
        for layer in MemoryLayer:
            items = self.store.get_by_layer(layer)
            stats["by_layer"][layer.value] = len(items)
        
        return stats
    
    def export_to_dict(self) -> Dict:
        """导出为字典"""
        return {
            "items": [item.to_dict() for item in self.store.items.values()],
            "stats": self.get_stats(),
        }
    
    def import_from_dict(self, data: Dict):
        """从字典导入"""
        for item_data in data.get("items", []):
            item = MemoryItem.from_dict(item_data)
            self.store.add(item)


# 便捷函数
def create_memory_engine(max_items: int = 1000) -> MemoryEngine:
    """创建记忆引擎"""
    return MemoryEngine(max_items)


def load_memory_from_file(file_path: str) -> MemoryEngine:
    """从文件加载记忆"""
    engine = MemoryEngine()
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        engine.import_from_dict(data)
    except FileNotFoundError:
        pass
    
    return engine


def save_memory_to_file(engine: MemoryEngine, file_path: str):
    """保存记忆到文件"""
    data = engine.export_to_dict()
    
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
