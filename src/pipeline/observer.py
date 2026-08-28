"""Observer - 9类事实提取器（借鉴inkOS Observer Agent）

从章节正文中过度提取9类事实，用于结构化状态更新：
1. Characters (角色) - 出场人物、状态变化
2. Locations (位置) - 场景、地点变化
3. Resources (资源) - 物品、装备、货币、功法等
4. Relationships (关系) - 人物关系变化
5. Emotions (情感) - 人物情感状态
6. Information (信息) - 新揭示的信息、秘密
7. Foreshadowing (伏笔) - 伏笔推进、新伏笔
8. Time (时间) - 时间流逝、时间节点
9. Physical State (物理状态) - 伤势、能力变化、身体状态
"""

from dataclasses import dataclass, field
from typing import Optional
from ..llm.client import MultiModelManager


# ========== 9类事实数据结构 ==========

@dataclass
class CharacterFact:
    """角色事实"""
    name: str
    action: str = ""           # 本章行为
    state_change: str = ""     # 状态变化
    emotion: str = ""          # 情感状态
    new_info: str = ""         # 新揭示信息

@dataclass
class LocationFact:
    """位置事实"""
    name: str
    description: str = ""      # 场景描述
    connected_from: str = ""   # 从哪里来
    connected_to: str = ""     # 到哪里去
    events: list = field(default_factory=list)  # 发生的事件

@dataclass
class ResourceFact:
    """资源事实"""
    name: str
    type: str = ""             # 物品/装备/货币/功法/丹药等
    owner: str = ""            # 当前持有者
    action: str = ""           # 获得/失去/使用/损坏
    description: str = ""

@dataclass
class RelationshipFact:
    """关系事实"""
    character_a: str
    character_b: str
    relation_type: str = ""    # 关系类型
    change: str = ""           # 关系变化
    reason: str = ""           # 变化原因

@dataclass
class EmotionFact:
    """情感事实"""
    character: str
    emotion: str = ""          # 情感类型
    intensity: str = ""        # 强度
    target: str = ""           # 情感对象
    trigger: str = ""          # 触发原因

@dataclass
class InformationFact:
    """信息事实"""
    content: str               # 信息内容
    revealed_by: str = ""      # 揭示者
    known_by: list = field(default_factory=list)  # 知情者
    importance: str = ""       # 重要程度

@dataclass
class ForeshadowingFact:
    """伏笔事实"""
    id: str = ""               # 伏笔ID（如已有）
    description: str = ""      # 伏笔描述
    action: str = ""           # planted/advanced/resolved/deferred
    chapter: int = 0           # 所在章节
    related_characters: list = field(default_factory=list)

@dataclass
class TimeFact:
    """时间事实"""
    duration: str = ""         # 本章时间跨度
    time_point: str = ""       # 特殊时间点
    sequence: str = ""         # 时间顺序信息

@dataclass
class PhysicalStateFact:
    """物理状态事实"""
    character: str
    injury: str = ""           # 伤势
    ability_change: str = ""   # 能力变化
    physical_state: str = ""   # 身体状态
    power_level: str = ""      # 实力等级变化


@dataclass
class ChapterFacts:
    """章节事实集合"""
    chapter_number: int
    characters: list = field(default_factory=list)      # List[CharacterFact]
    locations: list = field(default_factory=list)        # List[LocationFact]
    resources: list = field(default_factory=list)        # List[ResourceFact]
    relationships: list = field(default_factory=list)    # List[RelationshipFact]
    emotions: list = field(default_factory=list)         # List[EmotionFact]
    informations: list = field(default_factory=list)     # List[InformationFact]
    foreshadowing: list = field(default_factory=list)    # List[ForeshadowingFact]
    time: list = field(default_factory=list)             # List[TimeFact]
    physical_states: list = field(default_factory=list)  # List[PhysicalStateFact]

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "chapter_number": self.chapter_number,
            "characters": [self._to_dict(f) for f in self.characters],
            "locations": [self._to_dict(f) for f in self.locations],
            "resources": [self._to_dict(f) for f in self.resources],
            "relationships": [self._to_dict(f) for f in self.relationships],
            "emotions": [self._to_dict(f) for f in self.emotions],
            "informations": [self._to_dict(f) for f in self.informations],
            "foreshadowing": [self._to_dict(f) for f in self.foreshadowing],
            "time": [self._to_dict(f) for f in self.time],
            "physical_states": [self._to_dict(f) for f in self.physical_states],
        }

    def _to_dict(self, obj) -> dict:
        if hasattr(obj, '__dataclass_fields__'):
            return {k: getattr(obj, k) for k in obj.__dataclass_fields__}
        return obj

    def get_summary(self) -> str:
        """生成人类可读摘要"""
        parts = []
        if self.characters:
            names = [f.name for f in self.characters]
            parts.append(f"出场人物: {', '.join(names)}")
        if self.locations:
            locs = [l.name for l in self.locations]
            parts.append(f"场景: {', '.join(locs)}")
        if self.resources:
            res = [f"{r.name}({r.action})" for r in self.resources if r.action]
            if res:
                parts.append(f"资源变动: {', '.join(res)}")
        if self.relationships:
            rels = [f"{r.character_a}-{r.character_b}:{r.change}" for r in self.relationships if r.change]
            if rels:
                parts.append(f"关系变动: {', '.join(rels)}")
        if self.foreshadowing:
            hooks = [f"{f.description}({f.action})" for f in self.foreshadowing]
            parts.append(f"伏笔: {', '.join(hooks)}")
        return "\n".join(parts)


# ========== Observer 提示词 ==========

OBSERVER_PROMPT = """你是一位细致的小说事实提取专家。请从以下章节正文中提取9类事实。

## 章节正文
{chapter_content}

## 当前已知角色
{known_characters}

## 当前已知地点
{known_locations}

## 当前已知伏笔
{known_foreshadowing}

## 提取要求

请严格按照以下JSON格式提取事实，每类事实都是数组：

```json
{{
    "characters": [
        {{"name": "角色名", "action": "本章行为", "state_change": "状态变化", "emotion": "情感状态", "new_info": "新揭示信息"}}
    ],
    "locations": [
        {{"name": "地点名", "description": "场景描述", "connected_from": "从哪来", "connected_to": "到哪去", "events": ["事件1"]}}
    ],
    "resources": [
        {{"name": "资源名", "type": "物品/装备/货币/功法/丹药", "owner": "持有者", "action": "获得/失去/使用/损坏", "description": "描述"}}
    ],
    "relationships": [
        {{"character_a": "人物A", "character_b": "人物B", "relation_type": "关系类型", "change": "关系变化", "reason": "变化原因"}}
    ],
    "emotions": [
        {{"character": "角色", "emotion": "情感类型", "intensity": "强度", "target": "情感对象", "trigger": "触发原因"}}
    ],
    "informations": [
        {{"content": "信息内容", "revealed_by": "揭示者", "known_by": ["知情者"], "importance": "高/中/低"}}
    ],
    "foreshadowing": [
        {{"id": "已有ID或留空", "description": "伏笔描述", "action": "planted/advanced/resolved/deferred", "related_characters": ["相关人物"]}}
    ],
    "time": [
        {{"duration": "时间跨度", "time_point": "特殊时间点", "sequence": "时间顺序"}}
    ],
    "physical_states": [
        {{"character": "角色", "injury": "伤势", "ability_change": "能力变化", "physical_state": "身体状态", "power_level": "实力变化"}}
    ]
}}
```

## 提取原则
1. **过度提取**：宁可多提取，不要遗漏
2. **只提取正文明确提到的事实**，不要推测
3. **新角色必须提取**，包括路人甲如果有名有姓
4. **资源变动必须提取**，包括获得/失去/使用
5. **伏笔必须标注动作**：planted(新埋)/advanced(推进)/resolved(回收)/deferred(推迟)
6. **关系变化必须提取**，即使是微小变化
7. **情感变化必须提取**，标注触发原因
8. **返回纯JSON，不要额外说明**"""


class Observer:
    """Observer Agent - 9类事实提取器"""

    def __init__(self, model_manager: MultiModelManager):
        self.models = model_manager

    def extract_facts(self, chapter_number: int, chapter_content: str,
                      known_characters: Optional[list] = None, known_locations: Optional[list] = None,
                      known_foreshadowing: Optional[list] = None) -> ChapterFacts:
        """从章节正文中提取9类事实

        Args:
            chapter_number: 章节号
            chapter_content: 章节正文
            known_characters: 已知角色列表
            known_locations: 已知地点列表
            known_foreshadowing: 已知伏笔列表

        Returns:
            ChapterFacts 事实集合
        """
        client = self.models.get_writer()

        # 构建已知信息
        known_chars = "\n".join([f"- {c}" for c in (known_characters or [])]) or "暂无"
        known_locs = "\n".join([f"- {l}" for l in (known_locations or [])]) or "暂无"
        known_hooks = "\n".join([f"- {h}" for h in (known_foreshadowing or [])]) or "暂无"

        prompt = OBSERVER_PROMPT.format(
            chapter_content=chapter_content,
            known_characters=known_chars,
            known_locations=known_locs,
            known_foreshadowing=known_hooks,
        )

        messages = [{"role": "user", "content": prompt}]
        system = "你是一位细致的事实提取专家，只提取正文明确提到的事实。"

        response = client.chat_json(messages, system)

        # A parser-error envelope is not an empty fact set.  Returning an empty
        # set would let the deprecated compatibility pipeline continue as if
        # the model had found no facts and could eventually persist a misleading
        # success result.
        if not isinstance(response, dict):
            raise ValueError("FACT_EXTRACTION_OUTPUT_INVALID: expected a JSON object")
        if "error" in response:
            raise ValueError(
                "FACT_EXTRACTION_OUTPUT_INVALID: model returned invalid JSON"
            )

        # 解析为结构化事实
        return self._parse_facts(chapter_number, response)

    def _parse_facts(self, chapter_number: int, data: dict) -> ChapterFacts:
        """解析JSON为ChapterFacts"""
        facts = ChapterFacts(chapter_number=chapter_number)

        # 解析角色事实
        for item in data.get("characters", []):
            facts.characters.append(CharacterFact(
                name=item.get("name", ""),
                action=item.get("action", ""),
                state_change=item.get("state_change", ""),
                emotion=item.get("emotion", ""),
                new_info=item.get("new_info", ""),
            ))

        # 解析位置事实
        for item in data.get("locations", []):
            facts.locations.append(LocationFact(
                name=item.get("name", ""),
                description=item.get("description", ""),
                connected_from=item.get("connected_from", ""),
                connected_to=item.get("connected_to", ""),
                events=item.get("events", []),
            ))

        # 解析资源事实
        for item in data.get("resources", []):
            facts.resources.append(ResourceFact(
                name=item.get("name", ""),
                type=item.get("type", ""),
                owner=item.get("owner", ""),
                action=item.get("action", ""),
                description=item.get("description", ""),
            ))

        # 解析关系事实
        for item in data.get("relationships", []):
            facts.relationships.append(RelationshipFact(
                character_a=item.get("character_a", ""),
                character_b=item.get("character_b", ""),
                relation_type=item.get("relation_type", ""),
                change=item.get("change", ""),
                reason=item.get("reason", ""),
            ))

        # 解析情感事实
        for item in data.get("emotions", []):
            facts.emotions.append(EmotionFact(
                character=item.get("character", ""),
                emotion=item.get("emotion", ""),
                intensity=item.get("intensity", ""),
                target=item.get("target", ""),
                trigger=item.get("trigger", ""),
            ))

        # 解析信息事实
        for item in data.get("informations", []):
            facts.informations.append(InformationFact(
                content=item.get("content", ""),
                revealed_by=item.get("revealed_by", ""),
                known_by=item.get("known_by", []),
                importance=item.get("importance", ""),
            ))

        # 解析伏笔事实
        for item in data.get("foreshadowing", []):
            facts.foreshadowing.append(ForeshadowingFact(
                id=item.get("id", ""),
                description=item.get("description", ""),
                action=item.get("action", ""),
                chapter=chapter_number,
                related_characters=item.get("related_characters", []),
            ))

        # 解析时间事实
        for item in data.get("time", []):
            facts.time.append(TimeFact(
                duration=item.get("duration", ""),
                time_point=item.get("time_point", ""),
                sequence=item.get("sequence", ""),
            ))

        # 解析物理状态事实
        for item in data.get("physical_states", []):
            facts.physical_states.append(PhysicalStateFact(
                character=item.get("character", ""),
                injury=item.get("injury", ""),
                ability_change=item.get("ability_change", ""),
                physical_state=item.get("physical_state", ""),
                power_level=item.get("power_level", ""),
            ))

        return facts
