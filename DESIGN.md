# NovelForge（新小说） - AI小说创作平台

## 项目定位

融合 inkOS 与 webnovel-writer 两大项目的优点，打造一个完整的AI长篇小说创作平台。

## 核心优势对比

| 特性 | inkOS | webnovel-writer | NovelForge |
|------|-------|-----------------|------------|
| 独立运行 | ✅ Node.js CLI/Studio | ❌ 依赖Claude Code | ✅ Python独立运行 |
| 多模型支持 | ✅ OpenAI兼容 | ❌ 仅Claude | ✅ OpenAI兼容 |
| 审查打分 | ✅ 审计员系统 | ✅ 多维审查 | ✅ 双重门禁审查 |
| 记忆系统 | ✅ 三层记忆 | ✅ RAG+长期记忆 | ✅ 融合式记忆 |
| 连续创作 | ⚠️ 有限支持 | ❌ 无 | ✅ 完整连续创作模式 |
| 世界观向导 | ⚠️ Architect | ⚠️ /webnovel-init | ✅ 深度引导式向导 |
| 思维导图 | ❌ 无 | ❌ 无 | ✅ 自动生成 |
| 地图可视化 | ❌ 无 | ❌ 无 | ✅ 生图模型集成 |
| 多格式导出 | ✅ txt/md/epub | ❌ 无 | ✅ docx/md/txt |
| 联合审查 | ❌ 无 | ❌ 无 | ✅ 每5章联合审查 |
| 伏笔系统 | ✅ Zod schema | ✅ 追踪系统 | ✅ 融合式伏笔系统 |
| Web UI | ✅ Studio | ⚠️ Dashboard | ✅ 完整Web界面 |

## 架构设计

### 技术栈
- **后端**: Python 3.11+, FastAPI, SQLite
- **前端**: HTML/CSS/JS (内嵌), 可选React
- **LLM**: OpenAI-compatible API
- **存储**: SQLite + JSON + Markdown
- **导出**: python-docx, markdown, txt
- **可视化**: Mermaid.js (思维导图), vis.js (时间轴)

### 核心管线

```
世界观向导 → 规划器 → 编排器 → 写手 → 审查员 → 修订器 → 状态同步
                              ↑                              |
                              └──────── 不通过 ←─────────────┘
                                       (直到双重门禁通过)
```

### 双重门禁机制
1. **审查门禁**: 审查系统不返回针对性问题
2. **评分门禁**: 评分系统给出的分数 ≥ 93

### 联合审查机制（每5章）
- 剧情一致性检查
- 人物行为一致性
- 势力关系验证
- 地图设定符合性
- 故事连贯性
- 语言风格统一性
- 写作技法符合性

## 目录结构

```
新小说/
├── README.md
├── DESIGN.md
├── requirements.txt
├── run.py                    # 启动入口
├── config/
│   ├── default.yaml          # 默认配置
│   └── prompts/              # 提示词模板
│       ├── world_wizard.md
│       ├── chapter_write.md
│       ├── chapter_review.md
│       ├── joint_review.md
│       └── revise.md
├── src/
│   ├── __init__.py
│   ├── core/                 # 核心引擎
│   │   ├── __init__.py
│   │   ├── config.py         # 配置管理
│   │   ├── project.py        # 项目管理
│   │   ├── state.py          # 状态管理
│   │   ├── memory.py         # 记忆系统
│   │   └── models.py         # 数据模型
│   ├── llm/                  # LLM集成
│   │   ├── __init__.py
│   │   ├── client.py         # 统一LLM客户端
│   │   └── prompts.py        # 提示词管理
│   ├── wizard/               # 世界观向导
│   │   ├── __init__.py
│   │   └── guided_setup.py
│   ├── review/               # 审查与打分
│   │   ├── __init__.py
│   │   ├── reviewer.py       # 章节审查
│   │   ├── scorer.py         # 评分系统
│   │   └── joint_reviewer.py # 联合审查
│   ├── creation/             # 创作引擎
│   │   ├── __init__.py
│   │   ├── planner.py        # 规划器
│   │   ├── writer.py         # 写手
│   │   └── continuous.py     # 连续创作模式
│   ├── export/               # 导出系统
│   │   ├── __init__.py
│   │   ├── exporter.py       # 统一导出器
│   │   ├── docx_export.py
│   │   ├── md_export.py
│   │   └── txt_export.py
│   ├── visualization/        # 可视化
│   │   ├── __init__.py
│   │   ├── mindmap.py        # 思维导图
│   │   ├── timeline.py       # 时间轴
│   │   └── map_gen.py        # 地图生成
│   ├── web/                  # Web界面
│   │   ├── __init__.py
│   │   ├── app.py
│   │   └── static/
│   └── cli/                  # CLI界面
│       ├── __init__.py
│       └── main.py
└── templates/
    └── web/                  # Web模板
```
