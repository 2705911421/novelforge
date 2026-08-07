# NovelForge（新小说） - AI小说创作平台

> 融合 [inkOS](https://github.com/Narcooo/inkos) 与 [webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) 精华，打造下一代AI长篇小说创作系统

## 核心特性

### 双重门禁审查系统
借鉴 inkOS 审计员架构，每章创作完成后经过：
1. **审查门禁**: 审查系统不返回任何针对性问题
2. **评分门禁**: 9维度评分 ≥ 93分

只有同时满足两个条件，章节才视为合格。

### 连续创作模式
用户启动后系统自动根据规划进行连续创作（5-200章）：
- 每章：规划 → 创作 → 审查 → 修订 → 复审（循环直到双重门禁通过）
- 每5章：联合审查（剧情/人物/势力/地图/连贯性/风格/技法）
- 支持暂停、恢复、中断

### 世界观构建向导
引导用户与AI协同完成：
- 核心设定与矛盾
- 力量体系与世界规则
- 地理地图与势力分布
- 人物关系与成长弧
- 故事结构与卷规划
- 伏笔与钩子设计

### 多格式导出
- **DOCX**: 自动排版（首行缩进、行距、目录）
- **Markdown**: 结构化文档
- **TXT**: 纯文本

### 可视化系统
- **思维导图**: 世界观/角色/势力/地图/故事/伏笔六大分支
- **时间轴**: 交互式故事时间线
- **地图生成**: 配置生图模型后可生成势力/人物/剧情位置示意图

## 快速开始

### 安装
```bash
pip install -r requirements.txt
```

### 配置LLM
```bash
# 方式1: 环境变量
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"

# 方式2: 项目配置文件 (novelforge.yaml)
llm:
  primary:
    model: "gpt-4o"
    api_key: "your-key"
  review:
    model: "gpt-4o"
    api_key: "your-key"
```

### CLI使用
```bash
# 创建项目
python run.py init "我的小说" --genre "玄幻修仙"

# 导入已有设定
python run.py init "我的小说" --import-file world_setting.md

# 世界观向导
python run.py wizard <project_id>

# 写一章
python run.py write <project_id> 1

# 连续创作模式（10章）
python run.py continuous <project_id> --count 10

# 导出
python run.py export <project_id> --format docx

# 生成思维导图
python run.py mindmap <project_id>

# 生成时间轴
python run.py timeline <project_id>

# 查看状态
python run.py status <project_id>
```

### Web界面（推荐 - 完整对标 inkOS 的可视化 Studio）
```bash
# 方式1: 通过 CLI 启动（推荐）
python run.py serve --port 8000

# 方式2: 直接用 uvicorn 启动
python -m uvicorn src.web.studio:app --reload --port 8000
```
访问 http://localhost:8000

Studio 界面涵盖全部功能：我的创作 / 新建作品 / AI 助手 / 世界观向导 /
章节工作台 / 连续创作 / 思维导图 / 故事时间轴 / 伏笔与钩子 / 人物关系 /
数据分析 / 联合审查 / 真相文件 / 导出交付 / 题材库 / 模型配置 / 创作参数 /
导入素材 / 系统诊断。

## 架构设计

```
世界观向导 → 规划器 → 写手 → 审查员 → 修订器 → 状态同步
                              ↑                        |
                              └── 不通过（循环）──────┘
                              ↓ 通过（双重门禁）
                           每5章联合审查
```

### 记忆系统（三层融合）
| 层 | 用途 |
|---|---|
| SQLite | 章节摘要、事实、时间线事件 |
| JSON | 项目状态、审查报告 |
| Markdown | 世界观、角色、伏笔（人类可读） |

### 审查维度（9维度）
| 维度 | 权重 | 说明 |
|------|------|------|
| 剧情连贯性 | 15% | 与前文衔接、逻辑通顺 |
| 人物一致性 | 15% | 角色行为符合设定 |
| 世界设定 | 10% | 符合世界观、无设定冲突 |
| 写作质量 | 15% | 文笔、修辞、表达 |
| 节奏把控 | 10% | 情节推进速度、详略 |
| 伏笔处理 | 10% | 伏笔推进、新伏笔埋设 |
| 情感深度 | 10% | 情感表达、代入感 |
| 语言风格 | 10% | 风格统一、符合目标 |
| AI痕迹 | 5% | 消除AI生成痕迹 |

## 项目结构

```
新小说/
├── README.md
├── DESIGN.md               # 架构设计文档
├── requirements.txt
├── run.py                   # 启动入口
├── setup.py
├── config/
│   └── default.yaml         # 默认配置
├── src/
│   ├── core/                # 核心引擎
│   │   ├── models.py        # 数据模型
│   │   ├── config.py        # 配置管理
│   │   ├── project.py       # 项目管理
│   │   ├── memory.py        # 记忆系统
│   │   └── state.py         # 状态管理
│   ├── llm/                 # LLM集成
│   │   ├── client.py        # 统一客户端
│   │   └── prompts.py       # 提示词管理
│   ├── wizard/              # 世界观向导
│   ├── review/              # 审查与打分
│   │   ├── reviewer.py      # 章节审查（双重门禁）
│   │   └── joint_reviewer.py # 联合审查
│   ├── creation/            # 创作引擎
│   │   ├── planner.py       # 章节规划
│   │   ├── writer.py        # 章节写作
│   │   └── continuous.py    # 连续创作模式
│   ├── export/              # 导出系统
│   ├── visualization/       # 可视化
│   │   └── mindmap.py       # 思维导图+时间轴
│   ├── web/                 # Web界面
│   └── cli/                 # CLI界面
└── projects/                # 项目数据（自动创建）
```

## 对比分析

| 特性 | inkOS | webnovel-writer | NovelForge |
|------|-------|-----------------|------------|
| 独立运行 | Node.js CLI/Studio | 依赖Claude Code | **Python独立** |
| 多模型支持 | OpenAI兼容 | 仅Claude | **OpenAI兼容** |
| 审查打分 | 审计员系统 | 多维审查 | **双重门禁** |
| 连续创作 | 有限 | 无 | **完整模式** |
| 世界观向导 | Architect | /webnovel-init | **深度引导** |
| 思维导图 | 无 | 无 | **自动生成** |
| 多格式导出 | txt/md/epub | 无 | **docx/md/txt** |
| 联合审查 | 无 | 无 | **每5章** |

## 配置说明

详见 `config/default.yaml`，支持：
- LLM模型配置（主创作/审查/生图）
- 审查参数（通过分数、修订轮数、维度权重）
- 连续创作参数（章数范围、联合审查间隔）
- 导出格式
- 记忆系统（向量检索开关）
- 可视化（自动生图开关）

## 许可证

MIT License
