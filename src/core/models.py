"""核心数据模型定义"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import json
from datetime import datetime


class ChapterStatus(Enum):
    """章节状态"""
    PLANNED = "planned"         # 已规划
    DRAFTED = "drafted"         # 已起草
    REVIEWING = "reviewing"     # 审查中
    REVISING = "revising"       # 修订中
    APPROVED = "approved"       # 已通过
    EXPORTED = "exported"       # 已导出


class ReviewVerdict(Enum):
    """审查结论"""
    PASS = "pass"               # 通过
    NEEDS_REVISION = "needs_revision"  # 需修订
    MAJOR_ISSUES = "major_issues"      # 重大问题


@dataclass
class ReviewDimension:
    """审查维度评分"""
    name: str
    score: float  # 0-100
    weight: float = 0.0
    issues: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)


@dataclass
class ChapterReview:
    """章节审查结果"""
    chapter_number: int
    overall_score: float = 0.0
    verdict: ReviewVerdict = ReviewVerdict.NEEDS_REVISION
    dimensions: list = field(default_factory=list)  # List[ReviewDimension]
    specific_issues: list = field(default_factory=list)
    revision_suggestions: list = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def has_specific_issues(self) -> bool:
        """是否有针对性问题（门禁1）"""
        return len(self.specific_issues) > 0

    def meets_score_threshold(self, threshold: float = 93.0) -> bool:
        """是否达到分数阈值（门禁2）"""
        return self.overall_score >= threshold

    def passes_dual_gate(self, threshold: float = 93.0) -> bool:
        """是否通过双重门禁"""
        return not self.has_specific_issues() and self.meets_score_threshold(threshold)

    def to_dict(self) -> dict:
        return {
            "chapter_number": self.chapter_number,
            "overall_score": self.overall_score,
            "verdict": self.verdict.value,
            "dimensions": [
                {
                    "name": d.name,
                    "score": d.score,
                    "weight": d.weight,
                    "issues": d.issues,
                    "suggestions": d.suggestions
                }
                for d in self.dimensions
            ],
            "specific_issues": self.specific_issues,
            "revision_suggestions": self.revision_suggestions,
            "timestamp": self.timestamp
        }


@dataclass
class JointReview:
    """联合审查结果"""
    chapter_range: str  # e.g., "1-5"
    chapters: list = field(default_factory=list)  # List[int]
    overall_score: float = 0.0
    plot_consistency: dict = field(default_factory=dict)
    character_consistency: dict = field(default_factory=dict)
    faction_consistency: dict = field(default_factory=dict)
    map_consistency: dict = field(default_factory=dict)
    story_coherence: dict = field(default_factory=dict)
    style_consistency: dict = field(default_factory=dict)
    writing_technique: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class Character:
    """角色"""
    name: str
    role: str = ""  # 主角/配角/反派/龙套
    description: str = ""
    personality: str = ""
    background: str = ""
    relationships: dict = field(default_factory=dict)  # name -> relationship
    first_appearance: int = 0
    status: str = "alive"  # alive/dead/unknown
    faction: str = ""
    abilities: list = field(default_factory=list)
    notes: str = ""


@dataclass
class Faction:
    """势力"""
    name: str
    description: str = ""
    leader: str = ""
    members: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    territory: str = ""
    power_level: str = ""
    goals: list = field(default_factory=list)


@dataclass
class Location:
    """地点/地图节点"""
    name: str
    description: str = ""
    parent: str = ""
    connected_to: list = field(default_factory=list)
    faction: str = ""
    significance: str = ""
    first_appearance: int = 0


@dataclass
class Foreshadowing:
    """伏笔/钩子"""
    id: str
    description: str = ""
    planted_chapter: int = 0
    resolved_chapter: int = 0
    status: str = "open"  # open/progressing/resolved/deferred
    related_characters: list = field(default_factory=list)
    related_locations: list = field(default_factory=list)
    notes: str = ""


@dataclass
class Chapter:
    """章节"""
    number: int
    title: str = ""
    volume: int = 1
    arc: str = ""
    content: str = ""
    summary: str = ""
    status: ChapterStatus = ChapterStatus.PLANNED
    word_count: int = 0
    review: Optional[ChapterReview] = None
    revision_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    key_events: list = field(default_factory=list)
    characters_appeared: list = field(default_factory=list)
    locations_used: list = field(default_factory=list)
    foreshadowing_advanced: list = field(default_factory=list)
    task_brief: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()


@dataclass
class Volume:
    """卷"""
    number: int
    title: str = ""
    description: str = ""
    arcs: list = field(default_factory=list)  # List[Arc]
    target_chapters: int = 10
    themes: list = field(default_factory=list)


@dataclass
class Arc:
    """段弧"""
    name: str
    volume: int = 1
    description: str = ""
    chapters: list = field(default_factory=list)  # List[int]
    key_events: list = field(default_factory=list)
    themes: list = field(default_factory=list)


@dataclass
class WorldSetting:
    """世界设定"""
    name: str = ""
    genre: str = ""
    setting_description: str = ""
    time_period: str = ""
    core_conflict: str = ""
    power_system: str = ""
    world_rules: list = field(default_factory=list)
    cultures: list = field(default_factory=list)
    history: str = ""
    themes: list = field(default_factory=list)


@dataclass
class StoryProject:
    """小说项目"""
    id: str
    name: str
    genre: str = ""
    created_at: str = ""
    updated_at: str = ""
    world: WorldSetting = field(default_factory=WorldSetting)
    characters: dict = field(default_factory=dict)  # name -> Character
    factions: dict = field(default_factory=dict)     # name -> Faction
    locations: dict = field(default_factory=dict)    # name -> Location
    foreshadowing: dict = field(default_factory=dict) # id -> Foreshadowing
    volumes: list = field(default_factory=list)      # List[Volume]
    chapters: dict = field(default_factory=dict)     # number -> Chapter
    writing_style: str = ""
    target_word_count: int = 0
    author_intent: str = ""
    timeline: list = field(default_factory=list)     # 事件时间轴

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "genre": self.genre,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "world": self.world.__dict__,
            "characters": {k: v.__dict__ for k, v in self.characters.items()},
            "factions": {k: v.__dict__ for k, v in self.factions.items()},
            "locations": {k: v.__dict__ for k, v in self.locations.items()},
            "foreshadowing": {k: v.__dict__ for k, v in self.foreshadowing.items()},
            "volumes": [v.__dict__ for v in self.volumes],
            "chapters": {str(k): v.__dict__ for k, v in self.chapters.items()},
            "writing_style": self.writing_style,
            "target_word_count": self.target_word_count,
            "author_intent": self.author_intent,
            "timeline": self.timeline
        }

    def get_chapter_count(self) -> int:
        return len(self.chapters)

    def get_latest_chapter_number(self) -> int:
        if not self.chapters:
            return 0
        return max(self.chapters.keys())

    def get_open_foreshadowing(self) -> list:
        return [f for f in self.foreshadowing.values() if f.status in ("open", "progressing")]
