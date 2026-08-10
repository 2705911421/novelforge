# NovelForge

NovelForge 是一个面向中文长篇小说创作的本地优先 Python 工作台：把世界观、故事规划、章节写作、记忆检索、质量审查、修订、连续创作和文档导出串成一条可恢复的工作流。

项目提供 CLI 和 FastAPI/Studio Web 两种入口，支持 OpenAI 兼容的模型服务。作品数据、任务状态和创作附件默认保存在本地，不会随代码仓库提交。

> 项目仍在持续开发。功能范围和验证结论以 [`spec/features/`](spec/features/)、[`tests/`](tests/)、[`scripts/verify_features.py`](scripts/verify_features.py) 和 [`docs/IMPLEMENTATION_PROGRESS.md`](docs/IMPLEMENTATION_PROGRESS.md) 为准；README 的功能清单不代表生产就绪承诺。

## 核心能力

- 作品初始化与 Story Bible：通过 25 步向导逐步草拟、确认并发布世界观、人物、势力、地点、时间线和故事结构。
- 长篇规划与可视化：支持创作工作流、章节规划、情节画布、关系图、思维导图、世界地图和时间线。
- 写作流水线：规划、场景编排、章节生成、事实提取、审查、质量门禁、修订和版本化状态同步。
- 连续创作与任务恢复：批量章节、SQLite 持久化任务、checkpoint、暂停/恢复、失败重试和 SSE 进度回放。
- 记忆与 RAG：将章节、事实、时间线和参考文档索引到 SQLite，支持带来源、指纹和字符范围的检索结果。
- 文档与草稿导入：支持 TXT、Markdown、DOCX 等材料导入、解析、分块和长篇草稿分析。
- Studio 工作台：作品/章节管理、任务看板、章节版本、审查结果、模型配置、Prompt/Skill 扩展和系统诊断。
- 导入、导出与扩展：支持 Markdown、TXT、DOCX 导出，并提供交互式影像、翻译和图像生成等扩展模块。

## 工作流概览

```text
初始化作品 → Story Bible → 长篇规划 → 写作流水线 → 审查/质量门禁 → 修订
     ↑             ↓              ↓               ↓
参考文档导入 → 记忆/RAG ← SQLite 状态与任务 ← checkpoint/恢复
                                               ↓
                                          导出与交付
```

SQLite 是运行状态的主要权威边界：作品、章节版本、任务、checkpoint、审查结果、文档索引、模型调用记录和恢复状态都由持久化服务管理。

## 环境要求

- Python 3.11+
- SQLite（Python 标准库自带）
- 一个 OpenAI 兼容的模型服务（执行真实 AI 创作时需要；运行本地测试不需要）

## 快速开始

### 1. 安装依赖

PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

macOS/Linux：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. 配置模型服务

复制环境变量模板，并将凭据放在当前进程的环境变量中：

```powershell
Copy-Item .env.example .env
$env:OPENAI_API_KEY = "your-api-key"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
```

常用配置：

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
NOVELFORGE_LLM_MODEL=gpt-4o
NOVELFORGE_REVIEW_MODEL=gpt-4o
NOVELFORGE_ROOT=.
```

`.env` 仅用于本地保存模板，项目不会自动加载它；也可以直接配置 `config/default.yaml`。不要把真实 API Key 写入源代码、YAML、日志或 Git 提交。

### 3. 使用 CLI

```bash
# 查看所有命令
python run.py --help

# 创建本地作品
python run.py init "我的小说" --genre "玄幻修仙"

# 导入世界观材料或参考文档
python run.py init "我的小说" --import-file world_setting.md
python run.py ingest <project_id> <file_path> --type reference

# 查看或操作 Story Bible
python run.py bible <project_id> show
python run.py bible <project_id> publish

# 启动写作相关的持久化任务
python run.py wizard <project_id>
python run.py write <project_id> 1
python run.py continuous <project_id> --count 10

# 运行任务 worker、检索记忆并导出作品
python run.py worker
python run.py rag-search <project_id> "关键设定"
python run.py export <project_id> --format docx
```

### 4. 启动 Studio

```bash
python run.py serve --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。也可以直接使用 Uvicorn：

```bash
python -m uvicorn src.web.studio:app --reload --port 8000
```

## 数据边界

以下内容属于本地运行数据或生成物，默认不会提交：

- `projects/`：作品、SQLite 数据库和附件。
- `.novelforge-secrets/`、`.env`：本地凭据存储和环境配置。
- `.novelforge-backups/`、`exports/`、`studio/`、`test-output/`：备份、导出文件、Studio 会话和测试输出。

请不要提交真实作品、数据库、备份、日志或凭据。安全问题请参阅 [`SECURITY.md`](SECURITY.md)。

## 项目结构

```text
src/                核心运行时、CLI、写作流水线、Studio 和扩展模块
tests/              单元、集成、API、恢复和敌对路径测试
docs/               架构、阶段说明、审计和运行证据
spec/features/      功能合同与验收入口
scripts/             受保护验证和进度工具
config/              默认配置
.github/workflows/   GitHub Actions 验证流程
```

详细设计入口：

- [`DESIGN.md`](DESIGN.md)：整体设计摘要。
- [`docs/architecture/`](docs/architecture/)：系统、领域模型、AI 运行时、流水线、记忆、任务、图系统和备份恢复。
- [`docs/phases/`](docs/phases/)：各阶段实施说明与验收背景。
- [`docs/audit/`](docs/audit/) 与 [`docs/high-end-audit/`](docs/high-end-audit/)：审计、差距和运行证据。
- [`spec/features/`](spec/features/)：功能合同和验证入口。

## 开发与验证

安装依赖后，运行与变更相关的检查：

```bash
python -m pytest -q --tb=short
ruff check src tests
pyright src tests
python verify.py
python scripts/verify_features.py
python scripts/generate_progress.py --verify
```

真实第三方模型验证需要有效凭据；若未配置，应在提交或 Pull Request 中明确列出未运行的检查。受保护文件和变更约束见 [`CLAUDE.md`](CLAUDE.md) 与 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 许可证

本项目采用 [MIT License](LICENSE)。
