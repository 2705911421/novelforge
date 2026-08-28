# Slide Outline

## Meta
- Topic: NovelForge（新小说）AI 长篇小说创作工作台 · 产品发布 PPT
- Scenario: 产品发布·综合（面向潜在客户 / 合作伙伴 / 投资方）
- Content Source: free creation（基于项目 README / DESIGN / CONTEXT / CLAUDE 等真实资料）
- Style: 墨纸·极简编辑 — 暖宣纸底色 + 墨黑/靛蓝主色，衬线大标题，克制留白，文学气质
- Slide Count: 18
- Generated At: 2026-08-27

## Source Materials
N/A — generated from project knowledge（项目源码文档已读取：README.md / DESIGN.md / CONTEXT.md / CLAUDE.md）。

## Narrative Logic（整体叙事逻辑）
采用经典「问题 → 方案 → 证据 → 商业 → 行动」五幕结构：
1. **Why（为什么）**：行业痛点（世界观崩坏 / AI 失忆 / 质量失控 / 不可追溯）+ 用户真实需求。
2. **What（是什么）**：产品定位一句话 + 核心价值主张 + 完整工作流全景。
3. **Highlights（核心亮点）**：5 个按重要性排列的功能亮点（Story Bible、质量门禁、连续创作、StoryFlow、记忆/RAG/导出）。
4. **Proof（怎么证明）**：7 步产品演示流程 + 竞品差异化对比。
5. **Business（商业）**：目标市场数据 + 商业模式 + 发布时间线与路线图。
6. **CTA（行动）**：结尾行动号召。
视觉节奏：封面(深墨) → 中段定位陈述(深墨，强对比) → 结尾 CTA(深墨)，其余暖纸浅底，保证 3 张深色页的呼吸感。

## Slide-by-Slide Outline

1. **Slide 1 — Cover** — 产品名与发布主题的情绪化开场 | Layout: L02(BoldCover) | Dark: yes | Image: no | Chart: no
   - 标题：NovelForge 新小说
   - 副标题：AI 长篇小说创作工作台 · 产品发布
   - 元信息：2026 产品发布会 · 创作者 / 合作伙伴 / 投资方
   - 视觉：深墨底 + 宣纸笔触 SVG 装饰，产品名超大衬线字

2. **Slide 2 — Agenda（议程）** — 今天聊什么 | Layout: L19(List) | Dark: no | Image: no | Chart: no
   - 标题：今天，我们聊什么
   - 6 项：01 为什么需要 NovelForge / 02 产品是什么 / 03 五大核心亮点 / 04 现场演示 / 05 差异化与市场 / 06 路线图与行动
   - 视觉：编号列表 + 左侧竖线装饰

3. **Slide 3 — 行业痛点** — 长篇小说正被四件事拖垮 | Layout: L15(Matrix4) | Dark: no | Image: no | Chart: no
   - 标题：长篇小说创作，正在被四件事拖垮
   - 卡片：① 世界观崩坏（人物/时间线/伏笔越写越乱）② AI 失忆（聊天 AI 写长文人设崩、跑偏、无上下文）③ 质量失控（无门禁、无审查，水稿直出）④ 过程不可追溯（改了什么、为何改全凭记忆）
   - 视觉：2×2 卡片，每卡图标 + 红色强调

4. **Slide 4 — 用户需求** — 一幅好作品需要三种掌控 | Layout: L07(3Pillars) | Dark: no | Image: no | Chart: no
   - 标题：创作者真正想要的，是三件事
   - 三柱：① 对复杂世界的掌控（世界观/人物/时间线统一可视化）② 质量可控的 AI（明确规划与门禁下生成，不跑偏不水稿）③ 可追溯的过程（每步可暂停、可恢复、可审计）
   - 视觉：3 列等大图标卡

5. **Slide 5 — 产品定位（陈述）** — 把写小说变成工程 | Layout: L04(KeyStatement) | Dark: yes | Image: no | Chart: no
   - 陈述句：把"写小说"变成一项可恢复、可追溯的工程
   - 副：NovelForge 是本地优先的 AI 长篇小说创作工作台
   - 视觉：深墨底 + 大字陈述 + 细线装饰

6. **Slide 6 — 核心价值主张** — 为什么是 NovelForge | Layout: L16(IconRow) | Dark: no | Image: no | Chart: no
   - 标题：为什么是 NovelForge
   - 四价值：① 本地优先·数据私有（SQLite 全本地）② 质量可控（双重门禁+精准修订，不伪造通过）③ 完整工作流（设定→导出一条龙）④ 可恢复可追溯（任务队列/checkpoint/版本快照）
   - 视觉：4 图标横排

7. **Slide 7 — 工作流全景** — 一条可追溯的流水线 | Layout: L13(Process) | Dark: no | Image: no | Chart: no
   - 标题：一条完整、可追溯的创作流水线
   - 步骤：世界观向导 → 25 步 Story Bible → 长篇规划 → AI 写作流水线 → 双重质量门禁 → 连续创作 & 导出
   - 视觉：横向 6 步流程 + 箭头编号

8. **Slide 8 — 亮点1：Story Bible** — 创作基石 | Layout: L05(Concept+Visual) | Dark: no | Image: no | Chart: no
   - 标题：创作基石：25 步 Story Bible
   - 要点：草稿/顺序确认/发布 + SHA-256 版本快照；每步支持手填、AI 建议、优化、重生成；深度世界观向导由持久化 worker 执行
   - 视觉：左文右图（右侧 SVG 展示分层/进度结构）

9. **Slide 9 — 亮点2：质量门禁** — 质量靠门禁不靠运气 | Layout: L17(Data+Insight) | Dark: no | Image: no | Chart: yes
   - 标题：质量不靠运气，靠门禁
   - 要点：PRECHECK→编排→记忆检索→草稿→审查→修订→Story Commit；双重门禁 = 审查问题 + 9 维加权评分 ≥ 93 分；达上限进入 needs_author_decision，绝不伪造通过
   - 视觉：左侧流水线 SVG + 右侧 93 分门禁强调数字/评分条

10. **Slide 10 — 亮点3：连续创作** — 一次规划连续写 | Layout: L09(SingleKPI) | Dark: no | Image: no | Chart: no
    - 标题：一次规划，连续写 5–200 章
    - 大数字：200 章；副：可暂停·恢复·取消；每 5 章自动联合审查
    - 视觉：居中超大数字 KPI

11. **Slide 11 — 亮点4：StoryFlow** — 所有事实汇聚一张图 | Layout: L06(Concept+Visual) | Dark: no | Image: no | Chart: no
    - 标题：所有故事事实，汇聚一张图谱
    - 要点：思维导图/剧情/人物关系/时间线/世界地图/伏笔收敛到同一 Story Graph；六种视图共享；焦点子图 + 语义边；只读 Canon 模式
    - 视觉：右文左图（SVG 故事图谱节点示意）

12. **Slide 12 — 亮点5：记忆/RAG/导出** — 知识沉淀与交付 | Layout: L16(IconRow) | Dark: no | Image: no | Chart: no
    - 标题：知识沉淀与交付，一步到位
    - 四图标：① 多层记忆（Working/Episodic/Semantic/Operational）② RAG 可复现检索（BM25 + 可选 embedding）③ 多格式导入（TXT/MD/DOCX）④ 多格式导出（MD/TXT/DOCX/JSON/ink + 审查报告）
    - 视觉：4 图标横排

13. **Slide 13 — 产品演示流程** — 7 步看小说诞生 | Layout: L13(Process) | Dark: no | Image: no | Chart: no
    - 标题：7 步，看一部小说如何诞生
    - 步骤：① 初始化作品 ② 世界观向导 ③ 规划章节 ④ 单章写作(流水线) ⑤ 审查/修订门禁 ⑥ 连续创作 ⑦ 导出 DOCX
    - 视觉：横向 7 步流程

14. **Slide 14 — 竞品差异化对比** — 同样写小说我们不一样 | Layout: L08(Compare2) | Dark: no | Image: no | Chart: no
    - 标题：同样写小说，我们不一样
    - 对比维度（通用 AI 聊天/传统写作软件 vs NovelForge）：本地优先·数据私有 / 双重质量门禁 / 完整连续创作 / 统一故事图谱 / 可追溯 checkpoint
    - 视觉：三列对比表，高亮 NovelForge 列

15. **Slide 15 — 目标市场** — 数百万创作者的市场 | Layout: L11(ThreeKPIs) | Dark: no | Image: no | Chart: yes
    - 标题：一个数百万创作者的市场
    - 数据（行业估算，示意）：网文读者 ~5 亿 / 网文作者 ~2000 万 / AI 创作工具高速增长
    - 视觉：3 个 KPI 大数字 + 简洁趋势条

16. **Slide 16 — 商业模式** — 开源核心 + 多层变现 | Layout: L15(Matrix4) | Dark: no | Image: no | Chart: no
    - 标题：开源核心 + 多层变现
    - 四块：① 开源本���核心（免费·社区口碑）② Pro 订阅（云同步/高级模型/无限项目）③ API 按量 & 团队席位 ④ 出版/IP 分发服务
    - 视觉：2×2 卡片

17. **Slide 17 — 发布时间线与路线图** — 从今天到明年 | Layout: L13(Process/Timeline) | Dark: no | Image: no | Chart: no
    - 标题：从今天，到明年
    - 节点：2026 Q3 公开发布 v1.0（P0 能力 VERIFIED）/ 2026 Q4 StoryFlow 完善 + 云同步内测 / 2027 Q1 团队协作·出版分发 / 2027 Q2+ 互动影像·多语言翻译
    - 视觉：横向时间线 4 节点

18. **Slide 18 — 结尾行动号召** — 开始写下一部长篇 | Layout: L20(Closing) | Dark: yes | Image: no | Chart: no
    - 标题：现在，开始写你的下一部长篇
    - 副：加入内测 · 给 GitHub 一颗 Star · 成为合作伙伴
    - 联系：github.com/2705911421/novelforge
    - 视觉：深墨底 + 大字 CTA + 笔触装饰

## Visual Rhythm Notes
- 深色页位置：1（封面）、5（定位陈述）、18（结尾 CTA）—— 三张深墨页形成首尾与中段的呼吸节奏。
- 图表页位置：9（质量门禁 93 分评分强调）、15（市场 KPI 数字 + 趋势条）。
- 所有页为 1920×1080，内容距边缘 ≥100px；正文 ≥28px，标题 ≥56px；仅用 2 套字体（衬线标题 + 无衬线正文）。
- 装饰元素每页 ≥2（笔触 SVG、分隔细线、节点/图标），强化墨纸文学质感，避免纯白底。
- 市场数据标注为"行业估算·示意"，避免编造精确来源。
