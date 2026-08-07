"""
NovelForge 多层记忆引擎
支持三层记忆：工作记忆、情节记忆、语义记忆
"""

import json
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

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
    
    def get(self, item_id: str) -> Optional[MemoryItem]:
        """获取记忆项"""
        return self.items.get(item_id)
    
    def update(self, item: MemoryItem):
        """更新记忆项"""
        self.items[item.id] = item
    
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
    
    def __init__(self, max_items: int = 1000):
        self.store = MemoryStore()
        self.max_items = max_items
        self._counter = 0
    
    def _generate_id(self, prefix: str = "mem") -> str:
        """生成ID"""
        self._counter += 1
        return f"{prefix}_{self._counter:06d}"
    
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
                          characters: List[str] = None,
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
    
    def get_character_states(self, character_name: str = None) -> List[MemoryItem]:
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
                     focus_characters: List[str] = None) -> str:
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
