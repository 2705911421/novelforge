# NovelForge

NovelForge 是一个面向中文长篇小说创作的 Python 工作台：把世界观设定、故事规划、章节写作、记忆检索、质量审查、修订、持续创作和交付导出串成一条可恢复的工作流。

项目同时提供命令行入口和 FastAPI/Studio Web 界面。它支持 OpenAI 兼容的模型服务，创作项目数据默认保存在本地，不会随代码仓库提交。

> 当前项目仍在持续开发。功能范围和验证结论以 `spec/features/`、`tests/`、`scripts/verify_features.py` 及 `docs/IMPLEMENTATION_PROGRESS.md` 为准；不要仅依据 README 的功能描述判断生产就绪状态。

## 主要能力

- 世界观构建向导：设定、角色、势力、地图、故事结构和伏笔的结构化协作。
- 写作流水线：规划、生成、事实提取、审查、质量门禁、修订和状态同步。
- 连续创作：批量章节、checkpoint、暂停/恢复和失败恢复。
- 记忆与 RAG：基于 SQLite 的章节/事实/时间线存储，以及可追溯的文档分块检索。
- 审查与联合审查：多维度章节审查和跨章节一致性分析。
- Studio：作品管理、章节工作台、任务看板、实时 SSE 进度、模型配置和系统诊断。
- 导入与导出：TXT、Markdown、DOCX 文档导入，以及 Markdown、TXT、DOCX 导出。
- 备份与恢复：数据库备份、迁移前保护和恢复相关能力。

## 环境要求

- Python 3.11+
- SQLite（Python 标准库自带）
- 一个 OpenAI 兼容的模型服务（执行真实 AI 创作时需要；运行纯本地测试不需要）

## 快速开始

### 1. 安装

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

复制环境变量模板，并填写自己的凭据。项目会读取当前进程的环境变量；`.env` 是便于本地保存模板的未跟踪文件，不会被 Python 自动加载：

```powershell
Copy-Item .env.example .env
# 根据 .env 中的值设置当前 PowerShell 会话，例如：
$env:OPENAI_API_KEY = "your-api-key"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
```

支持的常用变量包括：

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
NOVELFORGE_LLM_MODEL=gpt-4o
NOVELFORGE_REVIEW_MODEL=gpt-4o
```

也可以编辑 `config/default.yaml`。不要把真实 API Key 写入 YAML、源代码、日志或 Git 提交；`.env` 已被 `.gitignore` 排除。

### 3. 使用 CLI

```bash
# 查看全部命令
python run.py --help

# 创建一个本地作品
python run.py init "我的小说" --genre "玄幻修仙"

# 导入世界观材料
python run.py init "我的小说" --import-file world_setting.md

# 启动世界观任务、写作任务或连续创作任务
python run.py wizard <project_id>
python run.py write <project_id> 1
python run.py continuous <project_id> --count 10

# 运行持久化任务 worker
python run.py worker

# 搜索已索引的参考材料并导出作品
python run.py rag-search <project_id> "关键设定"
python run.py export <project_id> --format docx
```

`projects/` 是本地运行数据目录，包含作品、SQLite 数据库和附件，默认不会提交到仓库。

### 4. 启动 Studio

```bash
python run.py serve --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。也可以直接使用：

```bash
python -m uvicorn src.web.studio:app --reload --port 8000
```

## 架构概览

```text
世界观向导 → Story Bible / 规划器 → 写作流水线 → 审查与质量门禁
      ↑                                      ↓
      └────── 记忆 / RAG / 状态 / 任务恢复 ← 修订
                                             ↓
                                      导出与交付
```

权威数据边界以 SQLite 为主，任务运行、章节版本、审查结果、文档索引和恢复状态都通过持久化服务处理。详细设计见：

- [`DESIGN.md`](DESIGN.md)：整体设计摘要。
- [`docs/architecture/`](docs/architecture/)：系统、领域模型、AI 运行时、流水线、记忆、任务、图系统和备份恢复设计。
- [`docs/phases/`](docs/phases/)：各阶段实施说明和验收背景。
- [`docs/audit/`](docs/audit/) 与 [`docs/high-end-audit/`](docs/high-end-audit/)：审计、差距和运行证据。
- [`spec/features/`](spec/features/)：功能合同和验收测试入口。

## 开发与验证

安装开发依赖后，可按下面的顺序运行检查：

```bash
python -m pytest -q --tb=short
ruff check src tests
pyright src tests
python verify.py
python scripts/verify_features.py
python scripts/generate_progress.py --verify
```

受保护的验收合同、验证脚本和测试入口见 [`CLAUDE.md`](CLAUDE.md)。修改受保护文件前需要明确的验证需求变更说明；不要通过删除断言、跳过测试或降低阈值来让检查通过。

## 数据与安全

- 作品数据和附件默认只保存在本地 `projects/`。
- 凭据应通过环境变量或应用提供的受保护配置边界提供；不要提交 `.env`、数据库、备份和日志。
- 如果误提交了密钥，应立即撤销并轮换该密钥，再清理 Git 历史。
- 安全问题请参阅 [`SECURITY.md`](SECURITY.md)。

## 参与贡献

请先阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)，了解本地检查、提交范围和受保护验证文件约束。提交问题或 Pull Request 时，请同时说明复现步骤、测试命令和已知限制。

## 许可证

本项目采用 [MIT License](LICENSE)。
