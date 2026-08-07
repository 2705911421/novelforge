"""管线模块 - 结构化编排系统

借鉴inkOS的Agent管线架构和webnovel-writer的Story System：
- Observer: 9类事实提取
- Reflector: JSON delta + immutable状态更新
- Composer: 规则栈编译 + 上下文选择
- ControlSurface: 控制面管理
- StorySystem: 合同驱动
- WritingRules: 25条创作规则 + 题材模板
- RAG: BM25检索系统
- Rhythm: Strand Weave节奏 + 追读力系统
"""

from .observer import Observer, ChapterFacts
from .reflector import Reflector
from .composer import Composer
from .control_surface import ControlSurface
from .story_system import StorySystem
from .rules import WritingRules
from .rag import RAGRetriever
from .rhythm import StrandWeaveTracker, ReaderEngagementTracker

__all__ = [
    "Observer", "ChapterFacts",
    "Reflector",
    "Composer",
    "ControlSurface",
    "StorySystem",
    "WritingRules",
    "RAGRetriever",
    "StrandWeaveTracker",
    "ReaderEngagementTracker",
]
