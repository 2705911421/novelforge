# 贡献指南

感谢你为 NovelForge 提交改进。项目面向本地长篇小说创作，贡献应优先保证数据持久化、失败恢复和可测试性。

## 开发环境

1. 使用 Python 3.11 或更高版本创建虚拟环境。
2. 安装 `requirements.txt` 中的依赖。
3. 复制 `.env.example` 为 `.env`，仅在本地填写模型凭据。
4. 运行 `python run.py --help` 确认 CLI 可启动。

## 提交前检查

```bash
python -m pytest -q --tb=short
ruff check src tests
pyright src tests
python verify.py
python scripts/verify_features.py
python scripts/generate_progress.py --verify
```

如果某个命令因本地环境、模型凭据或外部服务不可用而未运行，请在 Pull Request 中明确写出原因，不要用跳过或弱化测试代替验证。

## 变更边界

- 不要提交 `projects/`、本地数据库、备份、日志、缓存、浏览器产物或真实凭据。
- `spec/features/**`、`tests/acceptance/**` 和 `scripts/verify_*.py`、`scripts/generate_progress.py` 属于受保护验证资产。除非验证需求本身发生变化，否则不要修改。
- 涉及 Story System、写作流水线、Review Gate、Revision、Continuous Writing、Memory/RAG 或 Backup/Restore 的改动，需要覆盖成功、失败、持久化和恢复路径。
- 新增功能时同步补充测试、架构说明和用户可见文档。

## Pull Request

请使用清晰的标题和小范围提交，并在描述中包含：

- 做了什么、为什么做；
- 影响了哪些用户或数据边界；
- 运行过的检查及结果；
- 未运行的检查和已知限制。
