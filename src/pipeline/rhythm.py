"""节奏与追读力系统（借鉴webnovel-writer的Strand Weave + 追读力系统）

Strand Weave节奏系统：
- Quest（主线剧情）: 60%
- Fire（感情线）: 20%
- Constellation（世界观扩展）: 20%

节奏红线：
- Quest连续不超过5章
- Fire断档不超过10章
- Constellation断档不超过15章

追读力系统：
- Hook（钩子强度）
- Cool-point（爽点密度与质量）
- 微兑现（小期待的满足）
- 债务追踪（未兑现的承诺）
"""

from dataclasses import dataclass, field
from typing import Optional
from collections import deque


# ========== Strand Weave 节奏系统 ==========

@dataclass
class StrandInfo:
    """Strand信息"""
    name: str
    description: str
    ideal_ratio: float  # 理想占比
    max_consecutive: int  # 最大连续章数
    max_gap: int  # 最大断档章数

# 三种Strand定义
STRANDS = {
    "quest": StrandInfo(
        name="Quest",
        description="主线剧情",
        ideal_ratio=0.60,
        max_consecutive=5,
        max_gap=999,  # 主线不需要断档限制
    ),
    "fire": StrandInfo(
        name="Fire",
        description="感情线",
        ideal_ratio=0.20,
        max_consecutive=999,
        max_gap=10,
    ),
    "constellation": StrandInfo(
        name="Constellation",
        description="世界观扩展",
        ideal_ratio=0.20,
        max_consecutive=999,
        max_gap=15,
    ),
}


@dataclass
class ChapterStrand:
    """章节Strand标记"""
    chapter_number: int
    primary_strand: str = "quest"    # 主要Strand
    secondary_strands: list = field(default_factory=list)  # 次要Strand
    quest_score: float = 0.0
    fire_score: float = 0.0
    constellation_score: float = 0.0


class StrandWeaveTracker:
    """Strand Weave节奏追踪器"""

    def __init__(self):
        self.chapter_strands = []  # List[ChapterStrand]
        self.consecutive_counts = {"quest": 0, "fire": 0, "constellation": 0}
        self.gap_counts = {"quest": 0, "fire": 0, "constellation": 0}

    def record_chapter(self, strand: ChapterStrand):
        """记录章节Strand"""
        self.chapter_strands.append(strand)

        # 更新连续计数
        if strand.primary_strand == "quest":
            self.consecutive_counts["quest"] += 1
            self.consecutive_counts["fire"] = 0
            self.consecutive_counts["constellation"] = 0
        elif strand.primary_strand == "fire":
            self.consecutive_counts["fire"] += 1
            self.consecutive_counts["quest"] = 0
            self.consecutive_counts["constellation"] = 0
        else:
            self.consecutive_counts["constellation"] += 1
            self.consecutive_counts["quest"] = 0
            self.consecutive_counts["fire"] = 0

        # 更新断档计数
        if "fire" not in [strand.primary_strand] + strand.secondary_strands:
            self.gap_counts["fire"] += 1
        else:
            self.gap_counts["fire"] = 0

        if "constellation" not in [strand.primary_strand] + strand.secondary_strands:
            self.gap_counts["constellation"] += 1
        else:
            self.gap_counts["constellation"] = 0

    def check_rhythm_violations(self) -> list:
        """检查节奏违规"""
        violations = []

        # 检查Quest连续
        if self.consecutive_counts["quest"] > STRANDS["quest"].max_consecutive:
            violations.append({
                "type": "quest_consecutive",
                "message": f"主线剧情已连续{self.consecutive_counts['quest']}章，超过限制{STRANDS['quest'].max_consecutive}章",
                "suggestion": "建议插入感情线或世界观扩展",
            })

        # 检查Fire断档
        if self.gap_counts["fire"] > STRANDS["fire"].max_gap:
            violations.append({
                "type": "fire_gap",
                "message": f"感情线已断档{self.gap_counts['fire']}章，超过限制{STRANDS['fire'].max_gap}章",
                "suggestion": "建议加入感情线内容",
            })

        # 检查Constellation断档
        if self.gap_counts["constellation"] > STRANDS["constellation"].max_gap:
            violations.append({
                "type": "constellation_gap",
                "message": f"世界观扩展已断档{self.gap_counts['constellation']}章，超过限制{STRANDS['constellation'].max_gap}章",
                "suggestion": "建议加入世界观扩展内容",
            })

        return violations

    def get_strand_distribution(self, window: int = 10) -> dict:
        """获取最近N章的Strand分布"""
        recent = self.chapter_strands[-window:] if self.chapter_strands else []
        if not recent:
            return {"quest": 0, "fire": 0, "constellation": 0}

        total = len(recent)
        quest_count = sum(1 for s in recent if s.primary_strand == "quest")
        fire_count = sum(1 for s in recent if s.primary_strand == "fire")
        const_count = sum(1 for s in recent if s.primary_strand == "constellation")

        return {
            "quest": quest_count / total,
            "fire": fire_count / total,
            "constellation": const_count / total,
        }

    def suggest_next_strand(self) -> str:
        """建议下一章的Strand"""
        violations = self.check_rhythm_violations()

        # 优先修复违规
        for v in violations:
            if v["type"] == "quest_consecutive":
                # quest连续过多，推荐fire或constellation中更缺的
                dist = self.get_strand_distribution(20)
                fire_deficit = STRANDS["fire"].ideal_ratio - dist["fire"]
                const_deficit = STRANDS["constellation"].ideal_ratio - dist["constellation"]
                return "fire" if fire_deficit >= const_deficit else "constellation"
            if v["type"] == "fire_gap":
                return "fire"
            if v["type"] == "constellation_gap":
                return "constellation"

        # 检查分布是否偏离理想（正值 = 不足，应优先补充）
        dist = self.get_strand_distribution(20)

        quest_deficit = STRANDS["quest"].ideal_ratio - dist["quest"]
        fire_deficit = STRANDS["fire"].ideal_ratio - dist["fire"]
        const_deficit = STRANDS["constellation"].ideal_ratio - dist["constellation"]

        # 返回最不足的Strand（只考虑deficit为正的）
        candidates = [
            ("quest", quest_deficit),
            ("fire", fire_deficit),
            ("constellation", const_deficit),
        ]
        candidates = [(name, d) for name, d in candidates if d > 0]
        if candidates:
            return max(candidates, key=lambda x: x[1])[0]
        return "quest"


# ========== 追读力系统 ==========

@dataclass
class Hook:
    """钩子"""
    id: str
    description: str
    planted_chapter: int
    strength: float = 0.0  # 强度 0-1
    status: str = "open"   # open/advanced/resolved
    expected_payoff: str = ""  # 预期兑现方式

@dataclass
class CoolPoint:
    """爽点"""
    chapter_number: int
    description: str
    intensity: float = 0.0  # 强度 0-1
    type: str = ""  # power_up/revenge/revelation/romance等

@dataclass
class MicroPayoff:
    """微兑现"""
    chapter_number: int
    description: str
    related_hook: str = ""  # 关联的钩子ID
    satisfaction: float = 0.0  # 满意度 0-1

@dataclass
class Debt:
    """债务（未兑现的承诺）"""
    id: str
    description: str
    created_chapter: int
    urgency: float = 0.0  # 紧急度 0-1
    age: int = 0  # 存在章数


class ReaderEngagementTracker:
    """追读力追踪器"""

    def __init__(self):
        self.hooks = {}  # id -> Hook
        self.cool_points = []  # List[CoolPoint]
        self.micro_payoffs = []  # List[MicroPayoff]
        self.debts = {}  # id -> Debt

    def add_hook(self, hook: Hook):
        """添加钩子"""
        self.hooks[hook.id] = hook
        # 同时创建债务
        self.debts[hook.id] = Debt(
            id=hook.id,
            description=hook.description,
            created_chapter=hook.planted_chapter,
            urgency=0.5,
        )

    def advance_hook(self, hook_id: str, chapter_number: int):
        """推进钩子"""
        if hook_id in self.hooks:
            self.hooks[hook_id].status = "advanced"
            # 降低债务紧急度
            if hook_id in self.debts:
                self.debts[hook_id].urgency *= 0.8

    def resolve_hook(self, hook_id: str, chapter_number: int):
        """解决钩子"""
        if hook_id in self.hooks:
            self.hooks[hook_id].status = "resolved"
            # 移除债务
            if hook_id in self.debts:
                del self.debts[hook_id]

    def add_cool_point(self, point: CoolPoint):
        """添加爽点"""
        self.cool_points.append(point)

    def add_micro_payoff(self, payoff: MicroPayoff):
        """添加微兑现"""
        self.micro_payoffs.append(payoff)
        # 降低相关债务的紧急度
        if payoff.related_hook and payoff.related_hook in self.debts:
            self.debts[payoff.related_hook].urgency *= 0.9

    def update_debts(self, current_chapter: int):
        """更新债务（随时间增加紧急度）"""
        for debt in self.debts.values():
            debt.age = current_chapter - debt.created_chapter
            # 紧急度随时间增加
            debt.urgency = min(1.0, debt.urgency + 0.05)

    def get_engagement_score(self) -> float:
        """计算追读力分数"""
        score = 0.0

        # 钩子强度
        open_hooks = [h for h in self.hooks.values() if h.status == "open"]
        if open_hooks:
            avg_hook_strength = sum(h.strength for h in open_hooks) / len(open_hooks)
            score += avg_hook_strength * 0.3

        # 爽点密度
        if self.cool_points:
            recent_cool = [cp for cp in self.cool_points[-10:]]
            cool_density = len(recent_cool) / 10
            score += min(1.0, cool_density * 2) * 0.3

        # 债务管理
        if self.debts:
            avg_urgency = sum(d.urgency for d in self.debts.values()) / len(self.debts)
            score += avg_urgency * 0.2

        # 微兑现
        if self.micro_payoffs:
            recent_payoffs = [mp for mp in self.micro_payoffs[-10:]]
            payoff_density = len(recent_payoffs) / 10
            score += min(1.0, payoff_density * 3) * 0.2

        return min(1.0, score)

    def get_suggestions(self) -> list:
        """获取追读力建议"""
        suggestions = []

        # 检查高紧急度债务
        urgent_debts = [d for d in self.debts.values() if d.urgency > 0.7]
        if urgent_debts:
            for debt in urgent_debts[:3]:
                suggestions.append({
                    "type": "urgent_debt",
                    "message": f"债务 '{debt.description}' 已存在{debt.age}章，急需兑现",
                    "hook_id": debt.id,
                })

        # 检查爽点密度
        if len(self.cool_points) < 3:
            suggestions.append({
                "type": "cool_point_low",
                "message": "最近爽点密度较低，建议增加爽点",
            })

        # 检查微兑现
        if len(self.micro_payoffs) < 2:
            suggestions.append({
                "type": "micro_payoff_low",
                "message": "最近微兑现较少，建议增加小期待的满足",
            })

        return suggestions
