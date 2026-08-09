"""WritingRules - 创作规则体系（借鉴inkOS ~25条通用创作规则）

包含：
1. 25条通用创作规则（人物塑造/叙事技法/逻辑自洽/语言约束/去AI味）
2. 题材专属规则
3. 规则编译器
"""

from dataclasses import dataclass, field
from typing import Optional


# ========== 25条通用创作规则 ==========

UNIVERSAL_RULES = [
    # === 人物塑造 (5条) ===
    {
        "id": "char_01",
        "category": "人物塑造",
        "name": "性格一致性",
        "rule": "角色性格必须前后一致，不能突然转变而无铺垫",
        "check_points": ["角色言行是否符合其性格设定", "性格变化是否有合理铺垫"],
    },
    {
        "id": "char_02",
        "category": "人物塑造",
        "name": "动机驱动",
        "rule": "每个角色的行为必须有明确动机，不能为剧情服务而强行行动",
        "check_points": ["角色行为是否有合理动机", "动机是否与角色背景一致"],
    },
    {
        "id": "char_03",
        "category": "人物塑造",
        "name": "对话个性化",
        "rule": "对话必须符合角色身份、性格、教育背景，不能千人一面",
        "check_points": ["不同角色的说话方式是否有区别", "对话是否体现角色特征"],
    },
    {
        "id": "char_04",
        "category": "人物塑造",
        "name": "成长弧线",
        "rule": "主要角色必须有成长弧线，不能从头到尾没有变化",
        "check_points": ["角色是否有成长", "成长是否合理自然"],
    },
    {
        "id": "char_05",
        "category": "人物塑造",
        "name": "关系真实",
        "rule": "角色关系变化必须有铺垫和过程，不能突兀",
        "check_points": ["关系变化是否有铺垫", "关系发展是否自然"],
    },

    # === 叙事技法 (5条) ===
    {
        "id": "narr_01",
        "category": "叙事技法",
        "name": "展示而非讲述",
        "rule": "尽量通过行动、对话、细节展示，而非直接讲述",
        "check_points": ["是否过多使用'他感到...'", "是否通过细节展示情感"],
    },
    {
        "id": "narr_02",
        "category": "叙事技法",
        "name": "节奏把控",
        "rule": "情节推进节奏要合理，紧张与舒缓交替，不能一直高潮或一直平淡",
        "check_points": ["是否有张弛有度", "高潮与铺垫是否平衡"],
    },
    {
        "id": "narr_03",
        "category": "叙事技法",
        "name": "悬念设置",
        "rule": "章末必须留有悬念或钩子，吸引读者继续阅读",
        "check_points": ["章末是否有悬念", "悬念是否能引起好奇心"],
    },
    {
        "id": "narr_04",
        "category": "叙事技法",
        "name": "视角一致",
        "rule": "叙事视角要保持一致，不能随意切换",
        "check_points": ["视角是否统一", "切换视角是否有明确标记"],
    },
    {
        "id": "narr_05",
        "category": "叙事技法",
        "name": "场景描写",
        "rule": "场景描写要有画面感，调动多种感官",
        "check_points": ["是否有视觉描写", "是否调动听觉/嗅觉/触觉"],
    },

    # === 逻辑自洽 (5条) ===
    {
        "id": "logic_01",
        "category": "逻辑自洽",
        "name": "时间线连贯",
        "rule": "时间线必须连贯，不能出现时间矛盾",
        "check_points": ["时间顺序是否合理", "是否有时间穿越但未说明"],
    },
    {
        "id": "logic_02",
        "category": "逻辑自洽",
        "name": "地理一致",
        "rule": "地理位置和距离必须一致，不能自相矛盾",
        "check_points": ["地点位置是否一致", "移动时间是否合理"],
    },
    {
        "id": "logic_03",
        "category": "逻辑自洽",
        "name": "因果关系",
        "rule": "事件必须有合理的因果关系，不能无因无果",
        "check_points": ["事件是否有前因", "结果是否合理"],
    },
    {
        "id": "logic_04",
        "category": "逻辑自洽",
        "name": "设定遵守",
        "rule": "必须遵守已建立的世界观设定，不能自相矛盾",
        "check_points": ["是否违反已建立的规则", "新设定是否与旧设定冲突"],
    },
    {
        "id": "logic_05",
        "category": "逻辑自洽",
        "name": "战力平衡",
        "rule": "战斗结果必须符合战力设定，不能为剧情需要强行改变结果",
        "check_points": ["战斗结果是否符合战力", "是否有合理的解释"],
    },

    # === 语言约束 (5条) ===
    {
        "id": "lang_01",
        "category": "语言约束",
        "name": "用词准确",
        "rule": "用词要准确恰当，不能用错词或词不达意",
        "check_points": ["是否有用词错误", "是否词不达意"],
    },
    {
        "id": "lang_02",
        "category": "语言约束",
        "name": "句式多样",
        "rule": "句式要多样化，不能全是简单句或全是长句",
        "check_points": ["句式是否单一", "长短句是否搭配"],
    },
    {
        "id": "lang_03",
        "category": "语言约束",
        "name": "避免重复",
        "rule": "避免词语和句式的重复使用",
        "check_points": ["是否有高频重复词", "是否有相似句式"],
    },
    {
        "id": "lang_04",
        "category": "语言约束",
        "name": "标点正确",
        "rule": "标点符号使用要正确",
        "check_points": ["标点是否正确", "对话格式是否规范"],
    },
    {
        "id": "lang_05",
        "category": "语言约束",
        "name": "风格统一",
        "rule": "语言风格要统一，不能忽文忽白",
        "check_points": ["风格是否统一", "是否有风格突变"],
    },

    # === 去AI味 (5条) ===
    {
        "id": "deai_01",
        "category": "去AI味",
        "name": "避免套话",
        "rule": "避免使用AI常见的套话和模板化表达",
        "check_points": ["'不禁'/'竟然'/'居然'是否过多", "是否有模板化开头结尾"],
    },
    {
        "id": "deai_02",
        "category": "去AI味",
        "name": "避免说教",
        "rule": "避免通过角色之口进行说教和解释",
        "check_points": ["角色是否突然变成解说员", "是否通过对话强行解释设定"],
    },
    {
        "id": "deai_03",
        "category": "去AI味",
        "name": "情感真实",
        "rule": "情感表达要真实自然，不能过于戏剧化或虚假",
        "check_points": ["情感是否真实", "是否过于煽情"],
    },
    {
        "id": "deai_04",
        "category": "去AI味",
        "name": "细节真实",
        "rule": "细节描写要真实可信，不能凭空捏造不合常理的细节",
        "check_points": ["细节是否符合常识", "是否有不合理的细节"],
    },
    {
        "id": "deai_05",
        "category": "去AI味",
        "name": "避免过度修饰",
        "rule": "避免过度使用形容词和修饰语，保持简洁有力",
        "check_points": ["是否过度修饰", "是否堆砌形容词"],
    },
]


# ========== 题材专属规则 ==========

GENRE_RULES = {
    "玄幻修仙": {
        "name": "玄幻修仙",
        "rules": [
            {"id": "xh_01", "rule": "修为等级体系必须一致，不能越级战斗无合理解释"},
            {"id": "xh_02", "rule": "法宝/功法/丹药的设定必须前后一致"},
            {"id": "xh_03", "rule": "战斗描写要有层次感，不能一笔带过"},
            {"id": "xh_04", "rule": "修炼突破必须有铺垫，不能突然升级"},
            {"id": "xh_05", "rule": "天地规则要一致，不能自相矛盾"},
        ],
        "taboos": ["修为倒退无解释", "已死角色无理由复活", "法宝突然消失"],
    },
    "都市异能": {
        "name": "都市异能",
        "rules": [
            {"id": "ds_01", "rule": "异能不能过于破坏现实平衡"},
            {"id": "ds_02", "rule": "都市背景要真实可信"},
            {"id": "ds_03", "rule": "异能使用要有代价和限制"},
            {"id": "ds_04", "rule": "社会反应要合理"},
        ],
        "taboos": ["异能无限制使用", "普通人无理由接受超自然", "现代科技完全失效"],
    },
    "言情": {
        "name": "言情",
        "rules": [
            {"id": "yq_01", "rule": "感情线要有起伏，不能一帆风顺"},
            {"id": "yq_02", "rule": "误会与和解要合理，不能强行制造冲突"},
            {"id": "yq_03", "rule": "配角不能抢主角戏份"},
            {"id": "yq_04", "rule": "情感变化要有铺垫"},
        ],
        "taboos": ["无理由的误会", "强行拆散", "第三者无脑"],
    },
    "悬疑": {
        "name": "悬疑",
        "rules": [
            {"id": "xy_01", "rule": "线索要前后呼应，不能遗漏"},
            {"id": "xy_02", "rule": "推理要严密，不能有逻辑漏洞"},
            {"id": "xy_03", "rule": "悬念要层层递进"},
            {"id": "xy_04", "rule": "真相揭示要合理"},
        ],
        "taboos": ["线索断裂", "推理无依据", "真相突兀"],
    },
    "系统流": {
        "name": "系统流",
        "rules": [
            {"id": "xt_01", "rule": "系统规则必须一致，不能随意改变"},
            {"id": "xt_02", "rule": "系统奖励要合理，不能过于破坏平衡"},
            {"id": "xt_03", "rule": "系统不能成为万能解题器"},
        ],
        "taboos": ["系统规则前后不一", "系统无理由帮助主角", "系统奖励无上限"],
    },
    "无限流": {
        "name": "无限流",
        "rules": [
            {"id": "wx_01", "rule": "副本规则必须清晰"},
            {"id": "wx_02", "rule": "难度曲线要合理"},
            {"id": "wx_03", "rule": "团队配合要有逻辑"},
        ],
        "taboos": ["副本规则突然改变", "主角无理由通关", "队友无脑送死"],
    },
}


# ========== 规则编译器 ==========

@dataclass
class CompiledRules:
    """编译后的规则集"""
    universal_rules: list = field(default_factory=list)
    genre_rules: list = field(default_factory=list)
    book_rules: list = field(default_factory=list)
    chapter_rules: list = field(default_factory=list)
    total_count: int = 0

    def get_all_rules(self) -> list:
        """获取所有规则（按优先级排列）"""
        rules = []
        rules.extend(self.chapter_rules)    # 最高优先级
        rules.extend(self.book_rules)
        rules.extend(self.genre_rules)
        rules.extend(self.universal_rules)  # 最低优先级
        return rules

    def format_for_prompt(self) -> str:
        """格式化为提示词"""
        parts = []
        if self.chapter_rules:
            parts.append("## 本章特定规则")
            for r in self.chapter_rules:
                parts.append(f"- {r}")
        if self.book_rules:
            parts.append("## 本书规则")
            for r in self.book_rules:
                parts.append(f"- {r}")
        if self.genre_rules:
            parts.append("## 题材规则")
            for r in self.genre_rules:
                parts.append(f"- {r}")
        if self.universal_rules:
            parts.append("## 通用创作规则")
            for r in self.universal_rules:
                parts.append(f"- {r}")
        return "\n".join(parts)


class WritingRules:
    """创作规则管理器"""

    def __init__(self):
        self.universal_rules = UNIVERSAL_RULES
        self.genre_rules = GENRE_RULES

    def compile_rules(self, genre: str = "", book_rules: Optional[list] = None,
                      chapter_rules: Optional[list] = None) -> CompiledRules:
        """编译规则集

        Args:
            genre: 题材
            book_rules: 书级规则
            chapter_rules: 章节级规则

        Returns:
            CompiledRules 编译后的规则集
        """
        compiled = CompiledRules()

        # 1. 通用规则
        compiled.universal_rules = [r["rule"] for r in self.universal_rules]

        # 2. 题材规则
        if genre and genre in self.genre_rules:
            genre_data = self.genre_rules[genre]
            compiled.genre_rules = [r["rule"] for r in genre_data["rules"]]

        # 3. 书级规则
        if book_rules:
            compiled.book_rules = book_rules

        # 4. 章节级规则
        if chapter_rules:
            compiled.chapter_rules = chapter_rules

        compiled.total_count = (len(compiled.universal_rules) + len(compiled.genre_rules) +
                               len(compiled.book_rules) + len(compiled.chapter_rules))

        return compiled

    def get_genre_taboos(self, genre: str) -> list:
        """获取题材禁忌"""
        if genre in self.genre_rules:
            return self.genre_rules[genre].get("taboos", [])
        return []

    def get_rule_categories(self) -> list:
        """获取规则分类"""
        categories = set()
        for rule in self.universal_rules:
            categories.add(rule["category"])
        return sorted(categories)

    def get_rules_by_category(self, category: str) -> list:
        """按分类获取规则"""
        return [r for r in self.universal_rules if r["category"] == category]
