## 变更摘要

<!-- 说明做了什么以及为什么做。 -->

## 影响范围

<!-- 说明受影响的模块、数据边界、用户流程或迁移。 -->

## 验证

- [ ] `python -m pytest -q --tb=short`
- [ ] `ruff check src tests`
- [ ] `pyright src tests`
- [ ] `python verify.py`
- [ ] `python scripts/verify_features.py`
- [ ] `python scripts/generate_progress.py --verify`

未运行的检查及原因：

## 已知限制

<!-- 诚实列出未覆盖的路径、外部依赖或后续工作。 -->
