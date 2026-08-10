"""Built-in Skill catalog.

InkOS exposes a single governed workflow skill in its reference package and
keeps concrete workflow skills as regular ``SKILL.md`` packages.  NovelForge
ships the complete workflow catalog as durable, read-only-by-default Skills so
an author can select the same capabilities without downloading a package
first.  The instructions are contracts, not executable code.
"""

from __future__ import annotations

from typing import Any


def _skill(
    key: str,
    name: str,
    description: str,
    workflow: str,
    mission: str,
    limits: list[str],
    output: str,
) -> dict[str, Any]:
    forbidden = "\n".join(f"- {item}" for item in limits)
    instructions = f"""# Skill Contract: {name}

## Mission

{mission}

## Authority

Confirmed Story Bible and author-approved project state outrank imported draft
text, model inference, and convenience. Suggestions must be labelled; using a
Skill never authorizes an invisible change to canon, chapter truth, or files.

## Workflow

{workflow}

## Output Contract

{output}

## Limits and escalation

{forbidden}

If the supplied context is incomplete or contradictory, preserve the gap,
name the source, and ask for the smallest author decision needed to continue.

## Quality Gate

Before returning, verify source provenance, requested format, continuity
constraints, and whether every new claim is either sourced or explicitly a
proposal. Do not claim to have used a tool, read a file, or completed a
workflow that the host did not actually execute.
"""
    return {
        "name": name,
        "key": key,
        "description": description,
        "instructions": instructions,
        "enabled": True,
        "source": "builtin",
        "config": {"inkosWorkflow": key, "catalog": "inkos-complete"},
    }


BUILTIN_SKILLS = [
    _skill(
        "project-bootstrap",
        "作品初始化",
        "从题材、念头或规划资料建立可审阅的长篇创作工作区。",
        "1. 读取作者简报、题材规则和已有资料。\n2. 区分确认事实、待确认决策和模型建议。\n3. 生成 Story Bible 骨架与下一步清单。\n4. 只有作者明确确认后才进入可写状态。",
        "把一个模糊想法安全地变成可继续规划的作品基线。",
        ["不得把 AI 推断直接写成已确认设定。", "不得跳过 Story Bible 的确认门禁。"],
        "返回作品摘要、缺口、Story Bible 草案和作者确认清单。",
    ),
    _skill(
        "longform-planning",
        "长篇规划",
        "规划卷、篇章、章节和场景的因果结构与推进节奏。",
        "1. 盘点已确认事实和未决问题。\n2. 建立主线、人物线、伏笔线与时间线。\n3. 以目标—阻力—代价—不可逆变化拆解章节。\n4. 对每个转折标注前置条件和后果。",
        "把作者意图变成可执行、可审阅、可续写的长篇结构。",
        ["不得为填空而创造 canon。", "不得用大纲代替正文，也不得把只读视图当成事实。"],
        "返回分卷规划、章节卡、依赖关系、节奏风险与待确认项。",
    ),
    _skill(
        "longform-writing",
        "长篇写作",
        "按已确认计划写作章节正文，维持连续性与语言总览。",
        "1. 读取本章任务、上下文和语言边界。\n2. 先让场景目标与阻力落地。\n3. 通过动作、对白和细节推进，而不是复述大纲。\n4. 检查视角、时间、人物动机和章末压力。",
        "在不扩大变更范围的前提下，输出可供审查的章节草稿。",
        ["不得擅自改变确认事实。", "不得输出模板化的创作说明或审查意见混入正文。"],
        "返回指定格式的章节正文，另附明确要求的标题、字数或元数据。",
    ),
    _skill(
        "chapter-continuation",
        "章节续写",
        "从已存在的章节结尾延续下一章，处理悬念、余波和新目标。",
        "1. 提取结尾的未解决问题和人物状态。\n2. 检查下一章必须承接的事实。\n3. 设计新的主动目标与阻力。\n4. 让承接既有回应又产生新的因果压力。",
        "让续写像同一本书的自然下一步，而不是重新开局。",
        ["不得重复已经完成的冲突。", "不得为了制造悬念否认上一章已明确发生的结果。"],
        "返回续写计划或正文，并列出承接的源章节和未闭合线索。",
    ),
    _skill(
        "short-fiction",
        "短篇与短故事",
        "在有限篇幅内完成单一核心冲突、转折和余味。",
        "1. 选择一个主问题和一个不可替代的视角。\n2. 快速建立人物欲望与代价。\n3. 删除不服务核心冲突的支线。\n4. 用结局回照开头意象或问题。",
        "在长度限制内形成完整而非缩水的叙事闭环。",
        ["不得把长篇多线结构硬塞进短篇。", "不得用解释段落替代关键转折。"],
        "返回短篇结构卡或完整短篇，标注字数和核心余味。",
    ),
    _skill(
        "script-writing",
        "剧本写作",
        "将故事转译为可拍摄、可表演的场次、动作和对白。",
        "1. 建立场次目标、地点、时间和出场人物。\n2. 把心理信息转成可见动作或可听对白。\n3. 控制场次转折和信息揭示。\n4. 检查格式、镜头/舞台需求和制作可行性。",
        "交付结构清晰、可继续分镜或制作的剧本文本。",
        ["不得把不可拍摄的内心独白当成唯一信息。", "不得用影视术语掩盖故事因果缺口。"],
        "返回场次表、剧本页或分场提纲，遵守调用方格式。",
    ),
    _skill(
        "storyboard",
        "分镜规划",
        "把剧本场次拆成镜头、构图、运动、声音和情绪节拍。",
        "1. 读取场次目标和角色连续性。\n2. 按信息与情绪变化切镜头。\n3. 为每镜标注景别、视角、动作、声音和时长倾向。\n4. 检查轴线、视线、道具与服装连续性。",
        "为制作或图像生成提供可执行的视觉蓝图。",
        ["不得虚构剧本中未决定的关键事件。", "不得声称镜头已拍摄或图片已生成。"],
        "返回镜头表、连续性清单和需要作者确认的视觉决策。",
    ),
    _skill(
        "story-bible",
        "Story Bible 管理",
        "维护人物、世界、冲突、时间线、主题和语言总览的确认边界。",
        "1. 按 25 个 Story Bible 维度检查覆盖度。\n2. 标记 draft、confirmed、reopened 状态。\n3. 发现冲突时保留两个版本与来源。\n4. 只有作者确认后才建议发布或用于写作。",
        "让长期写作有稳定的权威事实层。",
        ["不得自动发布 AI 建议。", "不得用正文的偶然表述覆盖作者确认的设定。"],
        "返回缺口矩阵、冲突清单、待确认草案和影响范围。",
    ),
    _skill(
        "longform-review",
        "长篇审查",
        "对章节、卷或全书样本进行证据化的连续性、节奏、人物与语言审查。",
        "1. 声明审查范围和可用来源。\n2. 分维度检查事实、因果、人物、世界、时间、节奏和语言。\n3. 为问题附位置、短引文、严重程度和影响。\n4. 区分阻塞、可修复、润色和证据不足。",
        "让作者知道哪里偏离、为什么偏离、先修什么。",
        ["不得编造引文、分数或未读取章节。", "不得把建议当成自动修改。"],
        "返回稳定的审查 JSON 或报告，附优先级修复列表。",
    ),
    _skill(
        "longform-revision",
        "长篇修订",
        "按审查问题做最小、可追踪、不过度扩散的修订。",
        "1. 将问题映射到场景或句子。\n2. 先修根因，再修表象。\n3. 保留已经成立的节奏、声音和事实。\n4. 复查上下游影响并记录未修项。",
        "让修订可回退、可复审，并保持作者意图。",
        ["不得未经授权做全章重写。", "不得抹掉失败原因或修改报告以伪造通过。"],
        "返回修订文本、变更清单、未解决问题和复审建议。",
    ),
    _skill(
        "joint-review",
        "跨章联合审查",
        "发现跨章节伏笔、人物状态、时间线和风格的累积问题。",
        "1. 选择连续或主题相关的章节范围。\n2. 建立事实与承诺的跨章索引。\n3. 追踪设置、回收、转折和状态变化。\n4. 按影响范围排序修复，而非只看单章评分。",
        "补足单章审查看不见的长篇级断裂。",
        ["不得把采样结论扩大到未检查的全书。", "不得自动修改章节或 Story Bible。"],
        "返回跨章问题、证据链、影响章节和修复顺序。",
    ),
    _skill(
        "interactive-film",
        "互动影像",
        "把故事设计成节点、选项、条件、后果和可视化资产需求。",
        "1. 定义入口、节点状态和玩家/观众目标。\n2. 为每个选项给出条件、代价、后果和回收路径。\n3. 检查不可达节点、死循环和状态泄漏。\n4. 将镜头、对白、图片和音频需求分离标注。",
        "建立真实可运行、可审计的互动叙事图。",
        ["不得把选项文案当作已发生事实。", "不得声称不存在的资源已生成或已发布。"],
        "返回节点图、边条件、状态变量、资产清单和测试路径。",
    ),
    _skill(
        "open-world-play",
        "开放世界游玩",
        "以持久世界状态支持角色驱动的探索与叙事互动。",
        "1. 读取世界状态、角色动机和当前地点。\n2. 提供可选择的行动及其可见风险。\n3. 只推进玩家明确选择带来的状态变化。\n4. 写入前输出状态变更摘要并等待确认。",
        "让开放世界有自由度，同时保留持久状态的一致性。",
        ["不得替玩家选择关键行动。", "不得重置或隐藏持久世界状态。"],
        "返回场景、可选行动、风险、后果预告和待提交状态变更。",
    ),
    _skill(
        "branching-interactive",
        "分支互动小说",
        "规划有差异化因果路径、汇合点和结局条件的分支结构。",
        "1. 建立分支触发条件和共享前提。\n2. 确保每条路径改变角色选择或代价。\n3. 检查汇合是否抹平了选择后果。\n4. 运行最短路径、最长路径和异常状态检查。",
        "让互动选择有真实叙事价值且能被测试。",
        ["不得把分支写成换皮段落。", "不得遗漏结局条件或制造不可达分支。"],
        "返回分支图、路径差异、条件、结局和测试用例。",
    ),
    _skill(
        "fanfiction",
        "同人创作",
        "在用户指定的原作边界、角色关系和改编模式内进行衍生创作。",
        "1. 明确遵循原作、AU、OOC 研究或 CP 模式。\n2. 列出保留的 canon 与有意改写的差异。\n3. 检查角色声音、关系史和世界规则。\n4. 为原创内容标记与原作边界。",
        "让衍生创作尊重原作同时实现作者的新意图。",
        ["不得把未提供的原作细节当成确定事实。", "不得隐瞒 AU 或 OOC 改动。"],
        "返回 canon 约束、差异声明、故事计划或正文。",
    ),
    _skill(
        "spinoff",
        "衍生作品",
        "从已存在作品抽取稳定世界资产，建立有独立主线的新作品。",
        "1. 指定父作品和继承范围。\n2. 把可继承事实与新作变量分开。\n3. 设计新主角、新欲望和独立冲突。\n4. 检查父作结局、时间线和版权边界的影响。",
        "让衍生作品可独立阅读而不是父作资料拼接。",
        ["不得覆盖父作品章节正文。", "不得把新作推测写回父作 canon。"],
        "返回继承清单、差异清单、新作 Story Bible 骨架和首卷规划。",
    ),
    _skill(
        "style-imitation",
        "风格研究",
        "分析参考文本的可观察语言特征，形成可迁移而不复制的写作总览。",
        "1. 统计句长、段落、视角、对白、意象和节奏。\n2. 区分观察结果与主观解释。\n3. 只抽取短小示例用于说明。\n4. 把特征转成原创写作检查项。",
        "帮助作者掌握结构化语言特征，而不是复制具体表达。",
        ["不得生成大段近似或复刻参考文本。", "不得把模型猜测的作者意图当成事实。"],
        "返回语言总览、可执行检查项、短例证和不应模仿的表层标记。",
    ),
    _skill(
        "translation-localization",
        "翻译与本地化",
        "在保留叙事事实、人物声音和格式的前提下完成分段翻译。",
        "1. 读取术语表、角色语气和目标受众。\n2. 保留段落、占位符、实体和状态标签。\n3. 分离直译、文化转译和需确认项。\n4. 逐段记录状态并检查术语一致性。",
        "提供可审阅、可恢复、可导出的翻译结果。",
        ["不得删除原文信息。", "不得把文化解释偷偷写进正文。"],
        "返回源段、目标段、术语命中、疑点和完成状态。",
    ),
    _skill(
        "traceable-research",
        "可追溯资料研究",
        "为小说设定收集、整理和引用可追溯的资料，同时保持创作自由。",
        "1. 定义问题、时间范围和可信来源标准。\n2. 区分原始资料、二手解释和推断。\n3. 记录来源、摘录边界和适用范围。\n4. 将研究结论转换成可供作者审阅的设定选项。",
        "给创作提供可核验背景，而不是把研究权威伪装成故事真理。",
        ["不得编造来源或直接引用未提供的内容。", "不得把研究资料自动写进 Story Bible。"],
        "返回问题、来源清单、事实摘要、争议与创作选项。",
    ),
    _skill(
        "narrative-forecast",
        "剧情推演",
        "根据当前确认事实生成不同因果路径的未来分支。",
        "1. 固定当前章节、节点和角色状态。\n2. 生成至少两条真正不同的选择路径。\n3. 标注每个分支的前提、收益、风险和回收。\n4. 将推演画布与正式 canon 分离。",
        "帮助作者比较未来，而不是替作者决定未来。",
        ["不得把分支预测写成既成事实。", "不得修改 Story Bible 或章节正文。"],
        "返回分支、因果节点、风险、置信度和作者选择点。",
    ),
    _skill(
        "ai-content-audit",
        "AI 痕迹审查",
        "识别模板化、泛化、过度解释和不自然重复，并给出可定位的改进方向。",
        "1. 只分析提供的文本。\n2. 标记重复句式、空泛抽象、情绪标签和不合角色的解释。\n3. 结合语言总览判断是否真的是问题。\n4. 给出保留作者声音的局部修改建议。",
        "减少机械感而不把所有独特表达误判为问题。",
        ["不得把检测概率当成事实或抄袭结论。", "不得未经授权直接重写整章。"],
        "返回位置、模式、证据、影响和局部修复建议。",
    ),
    _skill(
        "genre-radar",
        "题材雷达",
        "基于本地题材库和作者作品状态提出创作方向，不冒充实时市场数据。",
        "1. 读取题材规则、作者约束和已有作品。\n2. 识别题材承诺、读者预期与可差异化空间。\n3. 给出概念、冲突和风险。\n4. 标明这是本地分析还是需要外部研究。",
        "把题材选择变成可解释的创作决策。",
        ["不得声称掌握实时平台排名或市场数据。", "不得以流行度替作者决定题材。"],
        "返回题材候选、核心卖点、规则风险、差异化方向和置信度。",
    ),
    _skill(
        "analytics-continuity",
        "作品分析与连续性",
        "对作品进度、字数、状态、伏笔和质量门禁做可复核分析。",
        "1. 声明数据窗口和统计口径。\n2. 识别章节、版本和审查状态。\n3. 统计趋势并回指具体章节。\n4. 将异常转成作者可执行的检查任务。",
        "用数据辅助创作判断，不用单一分数替代阅读。",
        ["不得编造缺失统计。", "不得把模型评分当作事实质量或市场保证。"],
        "返回指标、趋势、异常、证据章节和下一步动作。",
    ),
    _skill(
        "draft-import-repair",
        "初稿导入与偏移修复",
        "比较外部平台导入的长篇初稿与 Story Bible、语言总览，规划后续调整。",
        "1. 按 Story Bible > 语言总览 > 初稿正文读取来源。\n2. 声明文件数量、采样范围和缺失资料。\n3. 从剧情、人物、世界、时间、风格、节奏和承诺维度检查偏移。\n4. 按先修基础事实、再修连续性、最后修语言的顺序规划。",
        "让作者保留已有满意段落，同时知道后续如何收束跑偏内容。",
        ["不得把初稿反向覆盖更高优先级规划。", "不得自动改写或发布章节。", "不得声称已审阅未采样文件。"],
        "返回证据化偏移报告、章节发现、修复优先级、续写计划和局限。",
    ),
    _skill(
        "cover-direction",
        "封面方向",
        "把作品定位与视觉约束转成封面/宣传图的可执行 brief。",
        "1. 提取题材、主角、核心意象和读者承诺。\n2. 定义构图、色彩、字体留白和禁用元素。\n3. 分离封面正向提示词与负向提示词。\n4. 在生成前检查文字、版权和身份风险。",
        "为真实图像生成任务准备可验证的视觉方向。",
        ["不得声称已经生成图片。", "不得把临时概念写回故事事实。"],
        "返回封面 brief、提示词、负面提示词、尺寸和待确认项。",
    ),
    _skill(
        "novel-to-film",
        "小说影视化",
        "把长篇小说的稳定事实和情感主线转成影视化开发方案。",
        "1. 标出不可替代的主题、人物关系和关键转折。\n2. 区分保留、合并、删减和需要改编的内容。\n3. 建立季/集或电影时长内的结构。\n4. 检查改编后因果、人物主体性和制作边界。",
        "提供可审阅的影视化方向，不把改编稿反向覆盖小说正文。",
        ["不得把影视化取舍伪装成原作事实。", "不得声称项目已拍摄、融资或发行。"],
        "返回改编原则、人物/事件映射、分集或幕结构、删改风险和待确认项。",
    ),
    _skill(
        "story-player",
        "故事播放器",
        "把已发布或已确认的互动故事以可追踪状态播放给读者。",
        "1. 读取只读故事图和当前会话状态。\n2. 展示可用场景、选项和必要提示。\n3. 只提交读者明确选择带来的状态变化。\n4. 遇到断链、过期图或不可达节点时停止并报告。",
        "让 StoryPlayer 的阅读体验与持久叙事状态一致。",
        ["不得修改作者 canon。", "不得替读者选择。", "不得隐藏播放图断链或状态冲突。"],
        "返回当前场景、选择列表、状态摘要、来源节点和下一步动作。",
    ),
    _skill(
        "export-delivery",
        "导出交付",
        "将审查通过的章节、规划与互动资产整理成目标格式并保留交付记录。",
        "1. 确认导出范围、版本和质量门禁。\n2. 保留章节顺序、标题、元数据和引用边界。\n3. 检查目标格式、缺失资源和失败章节。\n4. 保存可复核的导出清单与报告路径。",
        "交付真实生成的文件，而不是只在界面显示一个完成状态。",
        ["不得把未通过门禁的内容标记为已通过。", "不得声称文件已生成但没有持久路径。"],
        "返回导出 manifest、文件路径、版本、过滤项、失败项和复核清单。",
    ),
]


# Acceptance set for the complete InkOS workflow catalog.  Keep this explicit
# and reviewable so a future edit cannot silently ship only a numeric subset.
INKOS_BUILTIN_SKILL_KEYS = frozenset({
    "project-bootstrap", "longform-planning", "longform-writing", "chapter-continuation",
    "short-fiction", "script-writing", "storyboard", "story-bible", "longform-review",
    "longform-revision", "joint-review", "interactive-film", "open-world-play",
    "branching-interactive", "fanfiction", "spinoff", "style-imitation",
    "translation-localization", "traceable-research", "narrative-forecast",
    "ai-content-audit", "genre-radar", "analytics-continuity", "draft-import-repair",
    "cover-direction", "novel-to-film", "story-player", "export-delivery",
})

if {item["key"] for item in BUILTIN_SKILLS} != set(INKOS_BUILTIN_SKILL_KEYS):
    raise RuntimeError("InkOS built-in Skill catalog and acceptance set are out of sync")

__all__ = ["BUILTIN_SKILLS", "INKOS_BUILTIN_SKILL_KEYS"]
