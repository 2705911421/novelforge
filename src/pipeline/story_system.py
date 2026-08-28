"""StorySystem - 合同驱动系统（借鉴webnovel-writer Story System）

.. deprecated::
    此模块使用文件 I/O（JSON）存储合同和事件，与 SQLite 存储并存。
    生产代码应使用 StoryRepository（基于 SQLite）替代此模块的持久化功能。
    此模块保留用于合同检查和合规性验证。

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
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


_LEGACY_STORY_SYSTEM_OPT_IN = "NOVELFORGE_ENABLE_LEGACY_CREATION_MODES"
_PRODUCTION_ENVS = {"production", "prod", "staging"}


def require_legacy_story_system() -> None:
    """Keep the file-backed StorySystem behind the development compatibility gate.

    The durable Host path is owned by ``StoryRepository`` and ``TaskRuntime``.
    This class remains importable for historical tooling, but constructing it
    creates ``story-system`` directories and therefore must never be an
    accidental production side effect.
    """
    enabled = os.environ.get(_LEGACY_STORY_SYSTEM_OPT_IN, "").strip().lower() in {"1", "true", "yes"}
    deployment = os.environ.get("NOVELFORGE_ENV", "development").strip().lower()
    if not enabled or deployment in _PRODUCTION_ENVS:
        raise RuntimeError(
            "LEGACY_STORY_SYSTEM_DISABLED: file-backed StorySystem is deprecated; "
            "use StoryRepository/TaskRuntime. Set "
            f"{_LEGACY_STORY_SYSTEM_OPT_IN}=1 only for development compatibility."
        )


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
    """防幻觉三定律 — STORY-007

    定律1: 大纲即法律 — 章节内容必须遵循预先制定的大纲/计划
    定律2: 设定即物理 — 角色行为和世界观必须与已确立的设定一致
    定律3: 发明需识别 — 新出现的实体必须被识别并注册到知识库
    """

    @staticmethod
    def check_outline_compliance(
        chapter_content: str,
        chapter_plan: dict,
        story_facts: list[dict] | None = None,
    ) -> list[dict]:
        """定律1: 大纲即法律 - 检查是否遵循大纲

        Args:
            chapter_content: 章节内容
            chapter_plan: 章节计划，包含 key_events、characters、locations 等
            story_facts: 已确立的故事事实列表

        Returns:
            违规列表，每项包含 severity、rule、description
        """
        violations = []

        if not chapter_plan:
            return violations

        content_lower = chapter_content.lower() if chapter_content else ""

        # 检查1: 关键事件覆盖
        plan_events = chapter_plan.get("key_events", [])
        if plan_events and content_lower:
            covered_events = []
            uncovered_events = []
            for event in plan_events:
                event_lower = event.lower()
                # 对于中文，使用子串匹配而非分词
                # 将事件拆分为2-gram进行模糊匹配
                if event_lower in content_lower:
                    # 精确匹配
                    covered_events.append(event)
                else:
                    # 使用n-gram进行模糊匹配
                    event_chars = list(event_lower)
                    if len(event_chars) >= 2:
                        # 检查2-gram的覆盖率
                        bigrams = [event_chars[i] + event_chars[i+1] for i in range(len(event_chars) - 1)]
                        match_count = sum(1 for bg in bigrams if bg in content_lower)
                        coverage = match_count / len(bigrams) if bigrams else 0
                        if coverage >= 0.5:
                            covered_events.append(event)
                        else:
                            uncovered_events.append(event)
                    else:
                        uncovered_events.append(event)

            if uncovered_events:
                violations.append({
                    "severity": "major",
                    "rule": "outline_compliance",
                    "description": f"计划中的关键事件未在章节中体现: {', '.join(uncovered_events[:3])}",
                })

        # 检查2: 计划角色是否出现
        plan_characters = chapter_plan.get("characters", [])
        if plan_characters and content_lower:
            missing_characters = []
            for char in plan_characters:
                char_lower = char.lower()
                if char_lower not in content_lower:
                    missing_characters.append(char)

            # 只有当超过一半的计划角色缺失时才报告
            if missing_characters and len(missing_characters) > len(plan_characters) * 0.5:
                violations.append({
                    "severity": "minor",
                    "rule": "outline_compliance",
                    "description": f"计划中的角色未在章节中出现: {', '.join(missing_characters[:3])}",
                })

        # 检查3: 章节标题一致性
        plan_title = chapter_plan.get("title", "")
        if plan_title and content_lower:
            title_keywords = [w for w in plan_title.lower().split() if len(w) > 2]
            if title_keywords:
                title_match = sum(1 for kw in title_keywords if kw in content_lower)
                if title_match == 0:
                    violations.append({
                        "severity": "minor",
                        "rule": "outline_compliance",
                        "description": f"章节内容与计划标题'{plan_title}'关联度低",
                    })

        return violations

    @staticmethod
    def check_setting_consistency(
        chapter_content: str,
        world_setting: dict,
        characters: dict,
        story_facts: list[dict] | None = None,
    ) -> list[dict]:
        """定律2: 设定即物理 - 检查设定一致性

        Args:
            chapter_content: 章节内容
            world_setting: 世界观设定
            characters: 角色设定字典
            story_facts: 已确立的故事事实列表

        Returns:
            违规列表
        """
        violations = []

        if not chapter_content:
            return violations

        content_lower = chapter_content.lower()

        # 检查1: 已死亡角色不能复活（除非有复活设定）
        if story_facts:
            dead_characters: set[str] = set()
            resurrection_rules: set[str] = set()

            for fact in story_facts:
                fact_content = fact.get("content", "").lower()
                fact_type = fact.get("fact_type", "")

                # 检测死亡事件
                if fact_type == "event" and any(kw in fact_content for kw in ["死亡", "去世", "牺牲", "被杀", "死了"]):
                    # 提取角色名
                    entities = fact.get("entities", [])
                    if isinstance(entities, str):
                        try:
                            import json as _json
                            entities = _json.loads(entities)
                        except (ValueError, TypeError):
                            entities = []
                    for entity in entities:
                        if isinstance(entity, str):
                            dead_characters.add(entity.lower())

                # 检测复活规则
                if fact_type == "rule" and any(kw in fact_content for kw in ["复活", "重生", "转世"]):
                    resurrection_rules.update(
                        e.lower() for e in (fact.get("entities", []) or [])
                        if isinstance(e, str)
                    )

            # 检查已死亡角色是否在章节中"复活"
            for dead_char in dead_characters:
                if dead_char in content_lower and dead_char not in resurrection_rules:
                    # 检查是否真的在行动（而不只是被提及）
                    action_patterns = [f"{dead_char}说", f"{dead_char}走", f"{dead_char}看",
                                       f"{dead_char}笑", f"{dead_char}站", f"{dead_char}坐"]
                    if any(pattern in content_lower for pattern in action_patterns):
                        violations.append({
                            "severity": "critical",
                            "rule": "setting_consistency",
                            "description": f"已死亡角色'{dead_char}'在章节中行动，违反设定",
                        })

        # 检查2: 角色性格一致性（基于角色设定）
        if characters:
            for char_name, char_info in characters.items():
                if not isinstance(char_info, dict):
                    continue

                char_name_lower = char_name.lower()
                if char_name_lower not in content_lower:
                    continue

                # 检查性格标签
                personality = char_info.get("personality", "")
                if personality:
                    # 检查是否有明显的性格冲突
                    personality_traits = [t.strip() for t in personality.split("，") if t.strip()]
                    for trait in personality_traits:
                        trait_lower = trait.lower()
                        # 检查反义词
                        antonyms: dict[str, list[str]] = {
                            "善良": ["残忍", "恶毒", "凶狠"],
                            "温柔": ["暴躁", "凶悍", "粗暴"],
                            "聪明": ["愚蠢", "笨拙"],
                            "勇敢": ["懦弱", "胆小"],
                            "诚实": ["虚伪", "欺骗"],
                        }
                        for positive, negatives in antonyms.items():
                            if positive in trait_lower:
                                for neg in negatives:
                                    if neg in content_lower:
                                        violations.append({
                                            "severity": "major",
                                            "rule": "setting_consistency",
                                            "description": f"角色'{char_name}'的性格'{trait}'与内容中的'{neg}'冲突",
                                        })

        # 检查3: 世界观规则违反
        if world_setting:
            rules = world_setting.get("rules", [])
            if isinstance(rules, list):
                for rule in rules:
                    if not isinstance(rule, str):
                        continue
                    rule_lower = rule.lower()
                    # 检查禁止事项
                    if any(kw in rule_lower for kw in ["禁止", "不允许", "不能", "不可"]):
                        # 提取禁止的关键词
                        forbidden = rule_lower.replace("禁止", "").replace("不允许", "").replace("不能", "").replace("不可", "").strip()
                        if forbidden and forbidden in content_lower:
                            violations.append({
                                "severity": "critical",
                                "rule": "setting_consistency",
                                "description": f"章节内容违反世界观规则: {rule}",
                            })

        return violations

    @staticmethod
    def check_new_entities(
        chapter_content: str,
        known_entities: set,
        story_facts: list[dict] | None = None,
    ) -> list[dict]:
        """定律3: 发明需识别 - 检查新实体

        Args:
            chapter_content: 章节内容
            known_entities: 已知实体集合
            story_facts: 已确立的故事事实列表

        Returns:
            新实体列表，每项包含 entity_type、entity_name、context
        """
        new_entities: list[dict] = []

        if not chapter_content:
            return new_entities

        # 从故事事实中提取已知实体
        if story_facts:
            for fact in story_facts:
                entities = fact.get("entities", [])
                if isinstance(entities, str):
                    try:
                        import json as _json
                        entities = _json.loads(entities)
                    except (ValueError, TypeError):
                        entities = []
                for entity in entities:
                    if isinstance(entity, str):
                        known_entities.add(entity.lower())

        # 简单的实体识别（基于中文命名模式）
        import re

        # 检测可能的人名（2-4个汉字的词，后面跟动作或对话）
        name_pattern = r'[\u4e00-\u9fff]{2,4}(?=[说走看笑站坐跑哭喊叫问道答点头摇头叹气转身])'
        potential_names = set(re.findall(name_pattern, chapter_content))

        # 检测可能的地名（XX城、XX山、XX国、XX宫、XX府）
        location_pattern = r'[\u4e00-\u9fff]{2,6}(?:城|山|国|宫|府|村|镇|谷|峰|湖|河|海|岛|森林|沙漠|草原|山脉)'
        potential_locations = set(re.findall(location_pattern, chapter_content))

        # 检测可能的组织/势力（XX门、XX派、XX帮、XX教、XX盟）
        faction_pattern = r'[\u4e00-\u9fff]{2,6}(?:门|派|帮|教|盟|会|堂|阁|殿|院)'
        potential_factions = set(re.findall(faction_pattern, chapter_content))

        # 检查哪些是新实体
        all_potential: dict[str, set[str]] = {
            "character": potential_names,
            "location": potential_locations,
            "faction": potential_factions,
        }

        for entity_type, entities in all_potential.items():
            for entity in entities:
                entity_lower = entity.lower()
                if entity_lower not in known_entities and len(entity) >= 2:
                    # 排除常见词汇
                    common_words = {
                        "一个", "这个", "那个", "什么", "怎么", "为什么", "可以",
                        "不能", "不要", "不是", "没有", "已经", "正在", "突然",
                        "忽然", "立刻", "马上", "终于", "居然", "竟然", "难道",
                        "也许", "大概", "可能", "一定", "必须", "需要", "应该",
                        "他们", "她们", "它们", "我们", "你们", "大家", "自己",
                        "这里", "那里", "哪里", "现在", "刚才", "以前", "以后",
                        "今天", "昨天", "明天", "早上", "中午", "晚上", "下午",
                    }
                    if entity_lower not in common_words:
                        new_entities.append({
                            "entity_type": entity_type,
                            "entity_name": entity,
                            "context": chapter_content[max(0, chapter_content.find(entity) - 20):
                                                       chapter_content.find(entity) + len(entity) + 20],
                        })

        return new_entities


# ========== StorySystem ==========

class StorySystem:
    """StorySystem - 合同驱动系统

    管理合同种子、运行时合同、章节提交和事件审计链。
    """

    def __init__(self, project_dir: Path):
        require_legacy_story_system()
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
                                   chapter_plan: Optional[dict] = None) -> RuntimeContract:
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
        """记录事件（原子写入）"""
        import tempfile
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
        # Atomic write: write to temp file then rename
        fd, tmp_path = tempfile.mkstemp(dir=str(self.events_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(events_file))
        except:
            os.unlink(tmp_path)
            raise

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
