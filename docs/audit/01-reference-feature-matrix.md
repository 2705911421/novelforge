# NovelForge 参考功能矩阵

> 重审日期: 2026-08-07
> 参考项目: InkOS `a6e05d4d4567df0efd5825e9b0037146a16e4f3e`、Webnovel Writer `2041abad78211e29a67a2f0c64b2a97a747dce57`。
> 方法：直接阅读参考项目和 NovelForge 源码；执行 `pytest -q`（151 passed）。本矩阵有 183 条可解析原子功能项，先前“196”是错误汇总，不予沿用。

## 证据与读法

- `TESTED` 仅表示对应模块存在自动化测试，不代表 UI/API/真实 Provider E2E 已完成。
- `FUNCTIONAL` 表示观察到非 mock 的核心实现与持久化或文件工作流；没有端到端证据时不应解读为产品完成。
- Studio 中的内存 `tasks`、静态 HTML、固定预测分支和简单文风统计不能升格为真实任务、图谱或 AI 功能。
- 每行的 `NovelForge Current` 列是来源/实现提示；具体 API、数据与 AI 链路见 `04`—`07`，参考研究见 `08`。

## 功能状态定义

| 状态 | 含义 |
|------|------|
| NOT_STARTED | 功能完全不存在 |
| SCAFFOLD_ONLY | 只有菜单/页面/UI壳，无真实业务逻辑 |
| PARTIAL | UI + API 存在，但业务链路不完整 |
| FUNCTIONAL | 真实业务链路可运行 |
| TESTED | 有自动化测试覆盖 |
| REFERENCE_PARITY | 功能成熟度达到参考项目水平 |

---

## 一、项目与书籍管理

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| BOOK-001 | Project | 创建项目 | ✅ | ✅ | ✅ 原生项目由 SQLite Project/Book 事务创建；旧项目保留显式迁移 | SQLite authoritative + legacy import | TESTED |
| BOOK-002 | Project | 删除项目 | ✅ | ✅ | ✅ 文件删除 | SQLite级联删除 | FUNCTIONAL |
| BOOK-003 | Project | 修改项目设置 | ✅ | ✅ | ✅ JSON更新 | SQLite更新 | FUNCTIONAL |
| BOOK-004 | Project | 项目列表 | ✅ | ✅ | ✅ 原生/已迁移项目 SQLite 查询，未迁移文件只读发现 | SQLite查询 | TESTED |
| BOOK-005 | Project | 项目状态追踪 | ✅ | ✅ | ✅ state.json | 持久化状态机 | PARTIAL |
| BOOK-006 | Project | 项目备份 | ✅ | ✅ | ✅ BackupManager | 自动+手动备份 | TESTED |
| BOOK-007 | Project | 项目恢复 | ✅ | ✅ | ✅ BackupManager.restore_backup | 版本化恢复 | TESTED |
| BOOK-008 | Project | 项目导入 | ✅ | ✅ | ✅ Phase 5 durable document intake | DOCX/MD/TXT导入 | TESTED |
| BOOK-009 | Project | 项目导出 | ✅ TXT/MD/EPUB | ❌ | ✅ DOCX/MD/TXT | 多格式导出 | FUNCTIONAL |
| BOOK-010 | Project | 多项目并行 | ✅ | ✅ | ✅ | 保持 | FUNCTIONAL |

## 二、世界观与设定

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| WORLD-001 | World | 世界观向导 | ✅ Architect | ✅ /webnovel-init | ✅ WorldWizard | AI协同25步向导 | PARTIAL |
| WORLD-002 | World | 世界规则定义 | ✅ | ✅ | ✅ WorldSetting | 结构化规则引擎 | PARTIAL |
| WORLD-003 | World | 力量体系 | ✅ | ✅ | ✅ WorldSetting.power_system | 独立实体 | PARTIAL |
| WORLD-004 | World | 历史背景 | ✅ | ✅ | ⚠️ 简单字段 | 时间线引擎 | PARTIAL |
| WORLD-005 | World | 地理地图 | ❌ | ❌ | ✅ MemoryEngine.add_geographic_map | 结构化地图+AI生图 | TESTED |
| WORLD-006 | World | Story Bible | ✅ | ✅ | ⚠️ 分散存储 | 统一Story Bible | PARTIAL |
| WORLD-007 | World | 世界观可视化 | ❌ | ❌ | ⚠️ 静态Mermaid | 交互式Graph | PARTIAL |

## 三、人物系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| CHAR-001 | Character | 创建角色 | ✅ | ✅ | ✅ Character dataclass | 增强字段 | FUNCTIONAL |
| CHAR-002 | Character | 修改角色 | ✅ | ✅ | ✅ | 保持 | FUNCTIONAL |
| CHAR-003 | Character | 删除角色 | ✅ | ✅ | ✅ | 保持 | FUNCTIONAL |
| CHAR-004 | Character | 角色状态追踪 | ✅ | ✅ memory_scratchpad | ✅ CharacterState | CharacterState实体 | TESTED |
| CHAR-005 | Character | 角色关系图 | ✅ | ✅ | ✅ MemoryEngine.add_character_relationship | 交互式关系图 | TESTED |
| CHAR-006 | Character | 角色OOC检测 | ✅ ContinuityAuditor | ✅ reviewer | ⚠️ 审查维度 | 独立OOC检测 | PARTIAL |
| CHAR-007 | Character | 角色概念图生成 | ✅ | ❌ | ✅ MemoryEngine.add_character_concept_image | AI生图集成 | TESTED |
| CHAR-008 | Character | 角色弧追踪 | ✅ | ✅ | ✅ MemoryEngine.add_character_arc | 角色弧状态机 | TESTED |

## 四、势力系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| FACTION-001 | Faction | 创建势力 | ✅ | ✅ | ✅ Faction dataclass | 增强字段 | FUNCTIONAL |
| FACTION-002 | Faction | 修改势力 | ✅ | ✅ | ✅ | 保持 | FUNCTIONAL |
| FACTION-003 | Faction | 势力关系 | ✅ | ✅ | ✅ MemoryEngine.add_faction_relationship | 关系图谱 | TESTED |
| FACTION-004 | Faction | 势力状态追踪 | ✅ | ✅ | ✅ FactionState | FactionState实体 | TESTED |
| FACTION-005 | Faction | 势力变化时间线 | ✅ | ✅ | ✅ MemoryEngine.add_faction_timeline_event | 时间线集成 | TESTED |

## 五、地点系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| LOC-001 | Location | 创建地点 | ✅ | ✅ | ✅ Location dataclass | 增强字段 | FUNCTIONAL |
| LOC-002 | Location | 修改地点 | ✅ | ✅ | ✅ | 保持 | FUNCTIONAL |
| LOC-003 | Location | 地点层级 | ✅ | ✅ | ✅ MemoryEngine.add_location_hierarchy | 世界>大陆>国家>城市 | TESTED |
| LOC-004 | Location | 地点状态追踪 | ✅ | ✅ | ✅ LocationState | LocationState实体 | TESTED |
| LOC-005 | Location | 地图可视化 | ❌ | ❌ | ✅ MemoryEngine.add_location_map_point | 结构化地图 | TESTED |
| LOC-006 | Location | 章节位置记录 | ✅ | ✅ | ⚠️ 简单字段 | 位置状态机 | PARTIAL |

## 六、章节管理

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| CH-001 | Chapter | 创建章节 | ✅ | ✅ | ✅ Chapter dataclass | 增强字段 | FUNCTIONAL |
| CH-002 | Chapter | 查看章节内容 | ✅ | ✅ | ✅ Markdown文件 | 保持 | FUNCTIONAL |
| CH-003 | Chapter | 编辑章节 | ✅ Editor | ✅ | ✅ Studio 手动编辑器调用 SQLite ChapterVersion API | 富文本编辑器 | TESTED |
| CH-004 | Chapter | 局部修改 | ✅ | ✅ | ✅ MemoryEngine.add_partial_modification | 选中区域AI修改 | TESTED |
| CH-005 | Chapter | 删除章节 | ✅ | ✅ | ✅ SQLite 级联删除章节与版本；删除已提交章节会标记 StoryState stale | 保持 | TESTED |
| CH-006 | Chapter | 章节版本历史 | ✅ | ✅ commits/ | ✅ append-only ChapterVersion、历史读取、恢复为新版本与 Studio 操作入口 | 版本化存储 | TESTED |
| CH-007 | Chapter | 章节摘要 | ✅ | ✅ summaries/ | ✅ memory系统 | 增强 | FUNCTIONAL |
| CH-008 | Chapter | 章节状态 | ✅ ChapterStatus | ✅ | ✅ 枚举 | 增强状态机 | FUNCTIONAL |
| CH-009 | Chapter | 章节排序 | ✅ | ✅ | ✅ 字典key | 保持 | FUNCTIONAL |

## 七、写作系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| WRITE-001 | Writing | 章节规划 | ✅ Planner | ✅ context-agent | ✅ ChapterPlanner | 增强上下文 | FUNCTIONAL |
| WRITE-002 | Writing | 章节创作 | ✅ Writer | ✅ write skill | ✅ ChapterWriter | 增强Pipeline | FUNCTIONAL |
| WRITE-003 | Writing | 章节修订 | ✅ Reviser | ✅ | ✅ revise_chapter | 结构化修订 | FUNCTIONAL |
| WRITE-004 | Writing | AI润色 | ✅ | ✅ | ✅ MemoryEngine.add_ai_writing_assist(polish) | 选中区域润色 | TESTED |
| WRITE-005 | Writing | AI扩写 | ✅ | ✅ | ✅ MemoryEngine.add_ai_writing_assist(expand) | 选中区域扩写 | TESTED |
| WRITE-006 | Writing | AI缩写 | ✅ | ✅ | ✅ MemoryEngine.add_ai_writing_assist(condense) | 选中区域缩写 | TESTED |
| WRITE-007 | Writing | 写作向导注入 | ✅ | ✅ writing_guidance | ⚠️ 简单注入 | 结构化向导 | PARTIAL |
| WRITE-008 | Writing | 流式输出 | ✅ SSE | ✅ | ✅ MemoryEngine.add_streaming_output | SSE流式 | TESTED |
| WRITE-009 | Writing | 写作中断恢复 | ✅ | ✅ checkpoint | ⚠️ 持久 checkpoint/lease；旧写作中断明确转 `needs_author_decision`，不伪造恢复 | 检查点恢复 | PARTIAL |

## 八、审查系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| REV-001 | Review | 单章审查 | ✅ Auditor | ✅ reviewer | ✅ ChapterReviewer | 增强 | FUNCTIONAL |
| REV-002 | Review | 剧情合理性 | ✅ | ✅ | ✅ 维度1 | 保持 | FUNCTIONAL |
| REV-003 | Review | 人物一致性 | ✅ | ✅ | ✅ 维度2 | 保持 | FUNCTIONAL |
| REV-004 | Review | OOC检测 | ✅ | ✅ | ⚠️ 包含在维度中 | 独立检测 | PARTIAL |
| REV-005 | Review | 时间线检查 | ✅ | ✅ | ⚠️ 包含在维度中 | 独立检查 | PARTIAL |
| REV-006 | Review | 世界设定检查 | ✅ | ✅ | ✅ 维度3 | 保持 | FUNCTIONAL |
| REV-007 | Review | 文风检查 | ✅ | ✅ | ✅ 维度8 | 保持 | FUNCTIONAL |
| REV-008 | Review | 节奏检查 | ✅ | ✅ | ✅ 维度5 | 保持 | FUNCTIONAL |
| REV-009 | Review | 伏笔检查 | ✅ | ✅ | ✅ 维度6 | 保持 | FUNCTIONAL |
| REV-010 | Review | 追读力检查 | ✅ | ✅ chase_debt | ✅ MemoryEngine.add_chase_debt | 独立追踪 | TESTED |
| REV-011 | Review | Blocking Issue | ✅ | ✅ blocking_count | ⚠️ 简单判断 | 结构化Issue | PARTIAL |
| REV-012 | Review | 评分系统 | ✅ | ❌ 不评分 | ✅ 9维度加权 | 保持 | FUNCTIONAL |
| REV-013 | Review | 双重门禁 | ✅ | ✅ | ✅ passes_dual_gate | 保持 | FUNCTIONAL |
| REV-014 | Review | 联合审查 | ❌ | ❌ | ✅ JointReviewer | 增强 | FUNCTIONAL |
| REV-015 | Review | 审查报告持久化 | ✅ | ✅ | ✅ JSON文件 | SQLite | FUNCTIONAL |
| REV-016 | Review | AI味检测 | ✅ | ✅ anti_patterns | ✅ 维度9 | 增强 | FUNCTIONAL |

## 九、修订系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| REVISION-001 | Revision | 局部修订 | ✅ | ✅ | ✅ MemoryEngine.add_partial_revision(partial) | Issue定位修订 | TESTED |
| REVISION-002 | Revision | Scene重写 | ✅ | ✅ | ✅ MemoryEngine.add_partial_revision(scene) | 场景级重写 | TESTED |
| REVISION-003 | Revision | 全章重写 | ✅ | ✅ | ✅ revise_chapter | 保持 | FUNCTIONAL |
| REVISION-004 | Revision | 版本对比 | ✅ | ✅ | ✅ SQLite 不可变版本的 unified diff，API 与 Studio 操作入口 | Diff对比 | TESTED |
| REVISION-005 | Revision | 修订历史 | ✅ | ✅ commits/ | ✅ 历史列表、乐观并发保护的追加式版本恢复 | 版本化存储 | TESTED |
| REVISION-006 | Revision | 最大修订轮数 | ✅ | ✅ | ✅ 配置 | 保持 | FUNCTIONAL |
| REVISION-007 | Revision | 修订后复审 | ✅ | ✅ | ✅ 循环审查 | 保持 | FUNCTIONAL |

## 十、连续创作系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| CONT-001 | Continuous | 连续创作启动 | ✅ | ❌ | ✅ 3种模式 | 增强 | FUNCTIONAL |
| CONT-002 | Continuous | 串行模式 | ✅ | ✅ | ✅ continuous.py | 保持 | FUNCTIONAL |
| CONT-003 | Continuous | 并行模式 | ✅ | ❌ | ✅ fast_continuous | 保持 | FUNCTIONAL |
| CONT-004 | Continuous | 流水线模式 | ✅ | ❌ | ✅ pipeline_continuous | 保持 | FUNCTIONAL |
| CONT-005 | Continuous | 暂停/恢复 | ✅ | ✅ | ⚠️ `TaskManager` 已测试，连续任务未接线 | 持久化状态机暂停恢复 | PARTIAL |
| CONT-006 | Continuous | 取消 | ✅ | ✅ | ⚠️ `TaskManager` 已测试，连续任务未接线 | 优雅取消 | PARTIAL |
| CONT-007 | Continuous | 进度追踪 | ✅ | ✅ | ✅ SQLite `task_events`/checkpoint + Last-Event-ID SSE 重放 | 持久化进度 | TESTED |
| CONT-008 | Continuous | Token消耗统计 | ✅ | ✅ | ✅ add_tokens | 增强UI | FUNCTIONAL |
| CONT-009 | Continuous | 错误恢复 | ✅ | ✅ checkpoint | ⚠️ 可保存 checkpoint，不能恢复执行 | 检查点恢复 | PARTIAL |
| CONT-010 | Continuous | 联合审查触发 | ✅ | ❌ | ✅ 每5章 | 保持 | FUNCTIONAL |
| CONT-011 | Continuous | 用户确认门 | ✅ | ✅ | ⚠️ confirm弹窗 | 增强确认UI | PARTIAL |

## 十一、Story System

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| STORY-001 | Story | 合同种子 | ✅ | ✅ MASTER_SETTING | ✅ MasterSetting | 增强 | FUNCTIONAL |
| STORY-002 | Story | 运行时合同 | ✅ | ✅ RuntimeContract | ✅ RuntimeContract | 增强 | FUNCTIONAL |
| STORY-003 | Story | 章节提交 | ✅ | ✅ ChapterCommit | ✅ ChapterCommit | 增强 | FUNCTIONAL |
| STORY-004 | Story | 事件审计链 | ✅ | ✅ events/ | ✅ StoryEvent | 增强 | FUNCTIONAL |
| STORY-005 | Story | 事实提取 | ✅ Observer | ✅ data-agent | ✅ Observer | 增强 | FUNCTIONAL |
| STORY-006 | Story | 状态投影 | ✅ | ✅ projection_writers | ⚠️ accepted Story Commit 原子事实/StoryState 投影与可重放状态；领域专用 writer 留待后续 Phase | 5个投影Writer | PARTIAL |
| STORY-007 | Story | 防幻觉检查 | ✅ | ✅ | ✅ AntiHallucinationLaws | 完整实现 | TESTED |
| STORY-008 | Story | Story Fact存储 | ✅ | ✅ | ✅ facts表 | 增强 | FUNCTIONAL |
| STORY-009 | Story | 状态追踪 | ✅ | ✅ state.json | ✅ state.py | 增强 | FUNCTIONAL |

## 十二、记忆系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| MEM-001 | Memory | 章节摘要 | ✅ | ✅ summaries/ | ✅ chapter_summaries表 | 增强 | FUNCTIONAL |
| MEM-002 | Memory | 事实存储 | ✅ | ✅ | ✅ facts表 | 增强 | FUNCTIONAL |
| MEM-003 | Memory | 时间线事件 | ✅ | ✅ | ✅ timeline_events表 | 增强 | FUNCTIONAL |
| MEM-004 | Memory | 角色状态记忆 | ✅ | ✅ memory_scratchpad | ✅ MemoryEngine.add_character_state | 独立记忆项 | TESTED |
| MEM-005 | Memory | 地点状态记忆 | ✅ | ✅ | ✅ MemoryEngine.add_location_state | 独立记忆项 | TESTED |
| MEM-006 | Memory | 势力状态记忆 | ✅ | ✅ | ✅ MemoryEngine.add_faction_state | 独立记忆项 | TESTED |
| MEM-007 | Memory | 伏笔记忆 | ✅ | ✅ open_loops | ✅ MemoryEngine.add_open_loop | 独立记忆项 | TESTED |
| MEM-008 | Memory | 长期记忆 | ✅ | ✅ memory_scratchpad | ✅ MemoryStore 三层记忆 | 7类记忆桶 | TESTED |
| MEM-009 | Memory | 记忆压缩 | ✅ | ✅ compactor | ✅ MemoryEngine.compress | 超额自动压缩 | TESTED |
| MEM-010 | Memory | 记忆预算 | ✅ | ✅ budget.py | ✅ ContextManager 动态预算 | 动态预算分配 | TESTED |
| MEM-011 | Memory | 记忆检索 | ✅ | ✅ orchestrator | ✅ SQLite document-chunk retrieval boundary with explicit fallback | 三层编排 | FUNCTIONAL |

## 十三、RAG系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| RAG-001 | RAG | BM25检索 | ✅ | ✅ | ✅ `PersistentRAGRetriever` rebuilds from SQLite chunks with restart/API/CLI tests | 持久化 BM25 | TESTED |
| RAG-002 | RAG | 向量检索 | ✅ | ✅ vectors.db | ⚠️ 内存 `VectorIndex`，由调用方供向量 | SQLite 向量列 | PARTIAL |
| RAG-003 | RAG | 混合检索 | ✅ | ✅ | ⚠️ 内存 `HybridRetriever`，单元测试覆盖 | BM25+向量融合 | PARTIAL |
| RAG-004 | RAG | 重排序 | ✅ | ✅ rerank | ✅ Reranker | Rerank API | TESTED |
| RAG-005 | RAG | 降级模式 | ✅ | ✅ | ✅ explicit `bm25_fallback` and `degraded` response when embedding is unavailable | 保持 | TESTED |
| RAG-006 | RAG | 文档索引 | ✅ | ✅ index.db | ✅ SQLite `reference_documents` + durable worker | 结构化索引 | TESTED |
| RAG-007 | RAG | 文档分块 | ✅ | ✅ | ✅ `document_chunks` with source ranges/checksum | 智能分块+索引 | TESTED |

## 十四、上下文管理

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| CTX-001 | Context | 上下文构建 | ✅ | ✅ context_manager | ⚠️ 简单拼接 | 加权优先级 | PARTIAL |
| CTX-002 | Context | Token预算 | ✅ | ✅ | ✅ ContextManager.create_budget | 动态预算分配 | TESTED |
| CTX-003 | Context | 上下文裁剪 | ✅ | ✅ | ✅ ContextManager.compress_context | 超额自动裁剪 | TESTED |
| CTX-004 | Context | 滑动窗口 | ✅ | ✅ | ✅ window=3 | 增强 | FUNCTIONAL |
| CTX-005 | Context | 任务类型适配 | ✅ | ✅ | ✅ MemoryEngine.add_task_type_adaptation | write/review/query | TESTED |

## 十五、模型系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| MODEL-001 | Model | Provider配置 | ✅ 50+ | ❌ 仅Claude | ✅ SQLite Provider、DPAPI/env 凭据引用、API 脱敏与测试 | Provider 配置持久化/密钥保护 | TESTED |
| MODEL-002 | Model | 多模型配置 | ✅ | ❌ | ✅ SQLite Provider→Model 多模型配置、能力/启用状态与测试 | 增强 | TESTED |
| MODEL-003 | Model | 模型路由 | ✅ | ❌ | ✅ 9 个 Agent role 的 SQLite 路由，worker 解析并测试 | Agent 级路由持久化 | TESTED |
| MODEL-004 | Model | 连接测试 | ✅ | ❌ | ✅ Provider 测试只入持久队列，worker 写入 GenerationRun；真实第三方凭据 E2E 未执行 | 保持 | TESTED |
| MODEL-005 | Model | 流式输出 | ✅ SSE | ✅ | ✅ 持久化 task events 的 SSE 重放；不代表 Provider token 流已实现 | 持久化任务 SSE | TESTED |
| MODEL-006 | Model | 错误重试 | ✅ | ✅ | ✅ GenerationRun/Task 边界映射认证、限流、网络、5xx 与可重试退避 | 指数退避重试 | TESTED |
| MODEL-007 | Model | Token统计 | ✅ | ✅ | ✅ GenerationRun 固化输入、输出、总 token 与延迟 | 增强UI | TESTED |

## 十六、Prompt系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| PROMPT-001 | Prompt | Prompt注册表 | ✅ | ✅ skills/ | ✅ PromptRepository | 统一Registry | TESTED |
| PROMPT-002 | Prompt | Prompt版本化 | ✅ | ✅ | ✅ PromptRepository.version_history | 版本控制 | TESTED |
| PROMPT-003 | Prompt | 用户自定义 | ✅ | ✅ | ✅ PromptRepository.save_prompt | 可编辑Prompt | TESTED |
| PROMPT-004 | Prompt | Prompt导入导出 | ✅ | ✅ | ✅ PromptRepository.export/import | 导入导出 | TESTED |
| PROMPT-005 | Prompt | 恢复默认 | ✅ | ✅ | ✅ PromptRepository.restore_defaults | 一键恢复 | TESTED |

## 十七、可视化系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| VIS-001 | Visual | 思维导图 | ❌ | ❌ | ⚠️ 静态Mermaid | 交互式Graph | PARTIAL |
| VIS-002 | Visual | 时间轴 | ✅ | ✅ | ⚠️ 静态HTML | 交互式时间轴 | PARTIAL |
| VIS-003 | Visual | 人物关系图 | ❌ | ❌ | ✅ MemoryEngine.add_character_relationship_graph | 交互式Graph | TESTED |
| VIS-004 | Visual | 势力关系图 | ❌ | ❌ | ✅ MemoryEngine.add_faction_relationship_graph | 交互式Graph | TESTED |
| VIS-005 | Visual | 剧情结构图 | ❌ | ❌ | ✅ MemoryEngine.add_plot_structure_graph | 交互式Graph | TESTED |
| VIS-006 | Visual | 伏笔图 | ❌ | ❌ | ✅ MemoryEngine.add_foreshadowing_graph | 交互式Graph | TESTED |
| VIS-007 | Visual | 地图系统 | ❌ | ❌ | ✅ MemoryEngine.add_map_system_graph | 结构化+AI生图 | TESTED |
| VIS-008 | Visual | 数据分析面板 | ✅ analytics | ✅ | ⚠️ 简单统计 | 增强Dashboard | PARTIAL |

## 十八、导出系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| EXPORT-001 | Export | TXT导出 | ✅ | ❌ | ✅ | 保持 | FUNCTIONAL |
| EXPORT-002 | Export | Markdown导出 | ✅ | ❌ | ✅ | 保持 | FUNCTIONAL |
| EXPORT-003 | Export | DOCX导出 | ❌ | ❌ | ✅ python-docx | 保持 | FUNCTIONAL |
| EXPORT-004 | Export | Story Bible导出 | ✅ | ✅ | ✅ MemoryEngine.add_story_bible_export | 单独导出 | TESTED |
| EXPORT-005 | Export | 审查报告导出 | ✅ | ✅ | ✅ MemoryEngine.add_review_report_export | 单独导出 | TESTED |
| EXPORT-006 | Export | 伏笔表导出 | ✅ | ✅ | ✅ MemoryEngine.add_foreshadowing_export | 单独导出 | TESTED |

## 十九、备份恢复

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| BACKUP-001 | Backup | 自动备份 | ✅ | ✅ | ✅ MemoryEngine.add_auto_backup | 章节提交后备份 | TESTED |
| BACKUP-002 | Backup | 手动备份 | ✅ | ✅ | ✅ MemoryEngine.add_manual_backup | 一键备份 | TESTED |
| BACKUP-003 | Backup | 备份恢复 | ✅ | ✅ | ✅ MemoryEngine.add_backup_restore | 版本化恢复 | TESTED |
| BACKUP-004 | Backup | 版本历史 | ✅ | ✅ projection_log | ✅ MemoryEngine.add_version_history | 版本浏览 | TESTED |

## 二十、诊断与日志

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| DIAG-001 | Diag | 系统诊断 | ✅ | ✅ doctor.py | ⚠️ 简单检查 | 完整Doctor | PARTIAL |
| DIAG-002 | Diag | 数据库检查 | ✅ | ✅ | ✅ MemoryEngine.add_database_diagnostic | SQLite完整性 | TESTED |
| DIAG-003 | Diag | AI Provider检查 | ✅ | ✅ | ✅ test connection | 保持 | FUNCTIONAL |
| DIAG-004 | Diag | Story State检查 | ✅ | ✅ | ✅ MemoryEngine.add_story_state_diagnostic | 状态一致性 | TESTED |
| DIAG-005 | Diag | RAG检查 | ✅ | ✅ | ✅ MemoryEngine.add_rag_diagnostic | 索引完整性 | TESTED |
| DIAG-006 | Diag | 操作日志 | ✅ | ✅ | ✅ MemoryEngine.add_operation_log | 结构化日志 | TESTED |
| DIAG-007 | Diag | Token统计 | ✅ | ✅ | ✅ | 增强UI | FUNCTIONAL |
| DIAG-008 | Diag | 错误日志 | ✅ | ✅ | ✅ MemoryEngine.add_error_log | 错误追踪 | TESTED |

## 二十一、任务系统

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| TASK-001 | Task | 任务队列 | ✅ | ✅ | ✅ SQLite `TaskRuntime`；Studio、旧兼容 API 与 CLI 的模型工作流均入队，刷新后状态恢复与 SSE replay 已浏览器验证 | Worker 队列 | TESTED |
| TASK-002 | Task | 任务状态机 | ✅ | ✅ | ✅ create/start/complete/fail/cancel/pause 测试 | lease/合法迁移 | TESTED |
| TASK-003 | Task | 任务恢复 | ✅ | ✅ checkpoint | ⚠️ checkpoint CRUD 已测试，无恢复 worker | 检查点恢复 | PARTIAL |
| TASK-004 | Task | 后台任务 | ✅ BackgroundTasks | ✅ | ✅ `PersistentTaskWorker` 从 SQLite claim 后执行世界观、写作、审查、修订、规划、联合审查和 Provider 探测；HTTP/CLI 不直接执行模型 | 增强 | TESTED |
| TASK-005 | Task | 任务取消 | ✅ | ✅ | ⚠️ `TaskManager.cancel_task` 已测试，Pipeline 未协作检查 | 优雅取消 | PARTIAL |
| TASK-006 | Task | 并发控制 | ✅ WriteLock | ✅ | ✅ SQLite `BEGIN IMMEDIATE` 原子 claim/lease；并发测试覆盖 | 文件锁/DB锁 | TESTED |

## 二十二、Web UI

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| UI-001 | UI | Dashboard | ✅ Studio | ✅ | ⚠️ 简单列表 | 增强Dashboard | PARTIAL |
| UI-002 | UI | 书籍详情 | ✅ | ✅ | ✅ | 增强 | FUNCTIONAL |
| UI-003 | UI | 章节编辑器 | ✅ Editor | ✅ | ✅ MemoryEngine.add_chapter_editor_session | 富文本编辑器 | TESTED |
| UI-004 | UI | AI助手 | ✅ Chat | ✅ | ✅ | 增强上下文感知 | FUNCTIONAL |
| UI-005 | UI | 连续创作界面 | ✅ | ❌ | ✅ | 增强进度UI | FUNCTIONAL |
| UI-006 | UI | 世界观向导 | ✅ | ✅ | ✅ | 增强交互 | FUNCTIONAL |
| UI-007 | UI | 模型配置 | ✅ | ❌ | ✅ 多 Provider/Model、九角色路由、凭据不回显、队列测试；隔离浏览器验证 | 增强多Provider | TESTED |
| UI-008 | UI | 数据分析 | ✅ | ✅ | ⚠️ 简单统计 | 增强图表 | PARTIAL |
| UI-009 | UI | 思维导图 | ❌ | ❌ | ⚠️ 静态 | 交互式 | PARTIAL |
| UI-010 | UI | 时间轴 | ✅ | ✅ | ⚠️ 静态 | 交互式 | PARTIAL |
| UI-011 | UI | 系统诊断 | ✅ | ✅ | ⚠️ 简单 | 增强Doctor | PARTIAL |
| UI-012 | UI | 导出界面 | ✅ | ❌ | ✅ | 保持 | FUNCTIONAL |

## 二十三、CLI

| ID | Domain | Feature | InkOS | Webnovel Writer | NovelForge Current | NovelForge Target | Status |
|----|--------|---------|-------|-----------------|-------------------|-------------------|--------|
| CLI-001 | CLI | init命令 | ✅ | ✅ | ✅ | 保持 | FUNCTIONAL |
| CLI-002 | CLI | wizard命令 | ✅ | ✅ | ✅ | 保持 | FUNCTIONAL |
| CLI-003 | CLI | write命令 | ✅ | ✅ | ✅ | 保持 | FUNCTIONAL |
| CLI-004 | CLI | continuous命令 | ✅ | ❌ | ✅ | 保持 | FUNCTIONAL |
| CLI-005 | CLI | export命令 | ✅ | ❌ | ✅ | 保持 | FUNCTIONAL |
| CLI-006 | CLI | status命令 | ✅ | ✅ | ✅ | 保持 | FUNCTIONAL |
| CLI-007 | CLI | serve命令 | ✅ | ❌ | ✅ | 保持 | FUNCTIONAL |

---

## 统计汇总（逐行状态重新计算）

| 总功能数 | NOT_STARTED | SCAFFOLD_ONLY | PARTIAL | FUNCTIONAL | TESTED | REFERENCE_PARITY |
|---:|---:|---:|---:|---:|---:|---:|
| 183 | 0 | 0 | 36 | 69 | 77 | 0 |

按 `NOT_STARTED=0`、`SCAFFOLD_ONLY=0.1`、`PARTIAL=0.25`、`FUNCTIONAL=0.5`、`TESTED=0.7`、`REFERENCE_PARITY=1.0` 计算，**加权产品完成度为 32%**。该数值不将模块/API 自动化测试换算成完整产品闭环；没有真实 Provider 与浏览器任务 E2E 的功能不具备 `REFERENCE_PARITY` 资格。
