"""StorySystem - 合同驱动系统（借鉴webnovel-writer Story System）

核心理念：
- 合同种子：项目初始化时生成的约束文件
- 运行时合同：每章写作前生成的约束
- CHAPTER_COMMIT：章节提交，包含accepted的事实
- 事件审计链：追踪所有变更事件

防幻觉三定律：
1. 大纲即法律——遵循大纲，不擅自发挥
2. 设定即物理——遵守设定，不自相矛盾
3. 发明需识别——新实体必须入库管理
"""

import json
import copy
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


# ========== 合同结构 ==========

@dataclass
class MasterSetting:
    """合同种子 - 项目初始化时生成"""
    project_name: str = ""
    genre: str = ""
    world_setting: dict = field(default_factory=dict)
    characters: dict = field(default_factory=dict)
    factions: dict = field(default_factory=dict)
    locations: dict = field(default_factory=dict)
    foreshadowing: dict = field(default_factory=dict)
    writing_rules: list = field(default_factory=list)
    anti_patterns: list = field(default_factory=list)  # 反模式配置
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class RuntimeContract:
    """运行时合同 - 每章写作前生成"""
    chapter_number: int = 0
    must_follow: list = field(default_factory=list)      # 必须遵循
    must_avoid: list = field(default_factory=list)        # 必须避免
    allowed_new_entities: list = field(default_factory=list)  # 允许的新实体类型
    character_constraints: dict = field(default_factory=dict) # 角色约束
    setting_constraints: dict = field(default_factory=dict)   # 设定约束
    pacing_requirements: str = ""
    emotional_requirements: str = ""

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class ChapterCommit:
    """章节提交"""
    chapter_number: int = 0
    timestamp: str = ""
    status: str = "pending"  # pending/accepted/rejected
    accepted_events: list = field(default_factory=list)     # 接受的事件
    state_deltas: dict = field(default_factory=dict)        # 状态增量
    entity_deltas: dict = field(default_factory=dict)       # 实体增量
    summary_text: str = ""                                  # 章节摘要
    new_entities: list = field(default_factory=list)        # 新发现的实体
    contract_violations: list = field(default_factory=list) # 合同违规

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class StoryEvent:
    """故事事件"""
    chapter_number: int = 0
    event_type: str = ""      # character_change/location_change/resource_change/relationship_change/foreshadowing_change
    description: str = ""
    details: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


# ========== 防幻觉三定律 ==========

class AntiHallucinationLaws:
    """防幻觉三定律"""

    @staticmethod
    def check_outline_compliance(chapter_content: str, chapter_plan: dict) -> list:
        """定律1: 大纲即法律 - 检查是否遵循大纲"""
        violations = []
        # 检查关键事件是否在计划中
        plan_events = set(chapter_plan.get("key_events", []))
        # 这里可以添加更详细的检查逻辑
        return violations

    @staticmethod
    def check_setting_consistency(chapter_content: str, world_setting: dict,
                                   characters: dict) -> list:
        """定律2: 设定即物理 - 检查设定一致性"""
        violations = []
        # 检查角色是否符合设定
        # 检查世界观是否一致
        # 这里可以添加更详细的检查逻辑
        return violations

    @staticmethod
    def check_new_entities(chapter_content: str, known_entities: set) -> list:
        """定律3: 发明需识别 - 检查新实体"""
        new_entities = []
        # 这里可以添加实体识别逻辑
        return new_entities


# ========== StorySystem ==========

class StorySystem:
    """StorySystem - 合同驱动系统

    管理合同种子、运行时合同、章节提交和事件审计链。
    """

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.story_dir = project_dir / "story-system"
        self.commits_dir = self.story_dir / "commits"
        self.events_dir = self.story_dir / "events"
        self.contracts_dir = self.story_dir / "contracts"
        self.story_dir.mkdir(parents=True, exist_ok=True)
        self.commits_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.contracts_dir.mkdir(parents=True, exist_ok=True)

    # ========== 合同种子 ==========

    def save_master_setting(self, setting: MasterSetting):
        """保存合同种子"""
        path = self.story_dir / "MASTER_SETTING.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(setting.to_dict(), f, ensure_ascii=False, indent=2)

    def load_master_setting(self) -> Optional[MasterSetting]:
        """加载合同种子"""
        path = self.story_dir / "MASTER_SETTING.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return MasterSetting(**data)
        return None

    def create_master_setting_from_project(self, project) -> MasterSetting:
        """从项目创建合同种子"""
        setting = MasterSetting(
            project_name=project.name,
            genre=project.genre,
            world_setting=project.world.__dict__,
            characters={name: char.__dict__ for name, char in project.characters.items()},
            factions={name: fac.__dict__ for name, fac in project.factions.items()},
            locations={name: loc.__dict__ for name, loc in project.locations.items()},
            foreshadowing={fid: fs.__dict__ for fid, fs in project.foreshadowing.items()},
            writing_rules=[
                "保持角色性格一致",
                "不要引入计划外的新设定",
                "注意伏笔的推进与埋设",
                "控制节奏，避免水字数",
                "对话要符合角色性格",
                "场景描写要有画面感",
                "章末留悬念或钩子",
                "避免AI味的表达方式",
            ],
            anti_patterns=[
                "角色突然获得无铺垫的能力",
                "已死亡角色无理由复活",
                "地理位置自相矛盾",
                "时间线混乱",
                "设定前后不一",
            ],
        )
        self.save_master_setting(setting)
        return setting

    # ========== 运行时合同 ==========

    def generate_runtime_contract(self, project, chapter_number: int,
                                   chapter_plan: dict = None) -> RuntimeContract:
        """生成运行时合同"""
        contract = RuntimeContract(chapter_number=chapter_number)

        # 从合同种子加载约束
        master = self.load_master_setting()
        if master:
            contract.must_follow.extend(master.writing_rules)
            contract.must_avoid.extend(master.anti_patterns)

        # 从章节计划加载约束
        if chapter_plan:
            if "must_keep" in chapter_plan:
                contract.must_follow.extend(chapter_plan["must_keep"])
            if "must_avoid" in chapter_plan:
                contract.must_avoid.extend(chapter_plan["must_avoid"])

        # 添加角色约束
        for name, char in project.characters.items():
            constraints = []
            if char.personality:
                constraints.append(f"性格: {char.personality}")
            if char.status != "alive":
                constraints.append(f"状态: {char.status}")
            if constraints:
                contract.character_constraints[name] = constraints

        # 保存合同
        self.save_runtime_contract(contract)

        return contract

    def save_runtime_contract(self, contract: RuntimeContract):
        """保存运行时合同"""
        path = self.contracts_dir / f"chapter-{contract.chapter_number:04d}.contract.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(contract.to_dict(), f, ensure_ascii=False, indent=2)

    def load_runtime_contract(self, chapter_number: int) -> Optional[RuntimeContract]:
        """加载运行时合同"""
        path = self.contracts_dir / f"chapter-{chapter_number:04d}.contract.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return RuntimeContract(**data)
        return None

    # ========== 章节提交 ==========

    def create_chapter_commit(self, chapter_number: int, facts=None,
                               state_delta=None, entity_delta=None,
                               summary: str = "") -> ChapterCommit:
        """创建章节提交"""
        commit = ChapterCommit(
            chapter_number=chapter_number,
            status="pending",
            summary_text=summary,
        )

        if facts:
            commit.accepted_events = [
                {"type": "character", "data": [f.__dict__ for f in facts.characters]},
                {"type": "location", "data": [f.__dict__ for f in facts.locations]},
                {"type": "resource", "data": [f.__dict__ for f in facts.resources]},
                {"type": "relationship", "data": [f.__dict__ for f in facts.relationships]},
                {"type": "foreshadowing", "data": [f.__dict__ for f in facts.foreshadowing]},
            ]

        if state_delta:
            commit.state_deltas = state_delta.to_dict() if hasattr(state_delta, 'to_dict') else state_delta

        if entity_delta:
            commit.entity_deltas = entity_delta

        return commit

    def accept_commit(self, commit: ChapterCommit):
        """接受章节提交"""
        commit.status = "accepted"
        self.save_commit(commit)

    def reject_commit(self, commit: ChapterCommit, violations: list):
        """拒绝章节提交"""
        commit.status = "rejected"
        commit.contract_violations = violations
        self.save_commit(commit)

    def save_commit(self, commit: ChapterCommit):
        """保存章节提交"""
        path = self.commits_dir / f"chapter_{commit.chapter_number:04d}.commit.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(commit.to_dict(), f, ensure_ascii=False, indent=2)

    def load_commit(self, chapter_number: int) -> Optional[ChapterCommit]:
        """加载章节提交"""
        path = self.commits_dir / f"chapter_{chapter_number:04d}.commit.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            commit = ChapterCommit()
            commit.__dict__.update(data)
            return commit
        return None

    def get_latest_commit(self) -> Optional[ChapterCommit]:
        """获取最新的accepted提交"""
        commits = sorted(self.commits_dir.glob("chapter_*.commit.json"), reverse=True)
        for commit_path in commits:
            with open(commit_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("status") == "accepted":
                commit = ChapterCommit()
                commit.__dict__.update(data)
                return commit
        return None

    # ========== 事件审计链 ==========

    def log_event(self, event: StoryEvent):
        """记录事件"""
        events_file = self.events_dir / f"chapter_{event.chapter_number:04d}.events.json"
        events = []
        if events_file.exists():
            with open(events_file, "r", encoding="utf-8") as f:
                events = json.load(f)
        events.append({
            "type": event.event_type,
            "description": event.description,
            "details": event.details,
            "timestamp": event.timestamp,
        })
        with open(events_file, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)

    def get_chapter_events(self, chapter_number: int) -> list:
        """获取章节事件"""
        events_file = self.events_dir / f"chapter_{chapter_number:04d}.events.json"
        if events_file.exists():
            with open(events_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def check_contract_compliance(self, commit: ChapterCommit) -> list:
        """检查合同合规性"""
        violations = []
        contract = self.load_runtime_contract(commit.chapter_number)
        if not contract:
            return violations

        # 检查合同违规
        for violation in commit.contract_violations:
            violations.append(violation)

        return violations
