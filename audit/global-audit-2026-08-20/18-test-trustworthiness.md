# Test Trustworthiness

## 本轮运行的测试与检查

| 命令/检查 | 结果 |
|---|---|
| .venv python -m pytest -q --tb=short | exit 0，1024 passed in 930.65s |
| scripts/generate_progress.py --verify | exit 0，P0 VERIFIED 5/5 |
| python verify.py | exit 0，core/LLM/wizard/review/creation/export/visualization/data/memory/config/state checks passed |
| check_protected_files.py --base origin/main | exit 0 |
| compileall src | exit 0 |
| ruff check src tests | exit 0 |
| git diff --check | exit 0 |
| JS syntax check | exit 0，JS_SYNTAX_OK |
| StoryFlow world snapshot suite | 107 passed |
| StoryFlow writing integration | 3 passed |
| adversarial suite | 23 passed |
| fable5 audit suite | 8 passed |
| real provider check-only | exit 0 but status BLOCKED_REAL_PROVIDER |
| pyright src tests | exit nonzero，32 errors，0 warnings |
| isolated browser smoke | page/graph/search 200，console 0 errors/0 warnings |

## 时间与基线

全量 1024 passed 覆盖当前 HEAD 的完整测试运行，耗时约 15 分 31 秒；这是本轮最强回归证据。此前历史文档中的 999 passed 等旧数字不作为当前状态。

## 可信部分

- 测试运行在当前 HEAD；
- protected files 未被篡改；
- full suite、focused suite、adversarial suite 交叉通过；
- 官方 verify_features 运行并输出 VERIFIED；
- 失败注入并非只读码：delete/provider/agent/review 均在临时数据库或纯内存对象中复现；
- 浏览器 fixture 与权威 DB 隔离。

## 不足与假成功风险

1. 全量绿不覆盖 pyright：32 个类型错误集中在新 StoryFlow graph/context/repository/world/studio 路径。
2. handoff 当前测试覆盖 success/persistence，没有同等级 failure/recovery。
3. 官方五项 P0 合约 VERIFIED 不能外推为 StoryFlow 全功能 VERIFIED。
4. real-provider 脚本本轮只做 check-only，未发送外部请求。
5. full 23-step browser acceptance 未重跑。
6. 当前权威 DB projection 缺失问题不会被普通单元测试自动暴露，因为 rebuild 可在副本中完成。

## 判定

Test trustworthiness 判定：PARTIAL。全量回归本身可信且通过；验证面尚未覆盖本轮发现的所有安全/恢复/生产边界。
