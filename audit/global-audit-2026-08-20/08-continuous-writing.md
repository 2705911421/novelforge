# Continuous Writing

## Runtime chain

docs/architecture/10-continuous-writing.md 与 pipeline 实现的主链为：

1. precheck；
2. plan；
3. child durable task；
4. context/generation；
5. review；
6. quality gate；
7. facts/state/commit；
8. parent continuation / author decision。

src/pipeline/writing_pipeline.py 的 _quality_gate 会根据 score、blocking issues、verdict 和 max revisions 进入 PASS、REVISION 或 COMPLETE/needs_author_decision。src/core/task_runtime.py 负责 durable state、lease、checkpoint、retry 和 child wait。

## Joint review 与作者决策

src/pipeline/continuous_writing.py：

- child 非 completed 会令 parent 进入 needs_author_decision；
- joint review 要求 pass、无 unresolved major/critical/blocking；
- max revisions 后不会伪造成功，而是等待作者；
- author decision 支持 retry、override、cancel；
- override 仍产生显式决定和审计信息。

## Simulation → writing handoff

src/web/studio.py:4849-4903：

- 校验 model setup、planning；
- 从 adoption 创建 ChapterIntent；
- 创建 next chapter；
- 创建 write-next durable task；
- 传入 adoption、intent、run config；
- 返回 canonicalMutation=false；
- 最终由 WritingPipeline/Review/StoryCommit 处理 Canon。

## 测试与证据

| 证据 | 结果 |
|---|---|
| tests/test_phase8_writing_pipeline.py | 11 passed |
| tests/test_phase9_review_pipeline.py + phase12 | 13 passed |
| tests/test_l27_l28_l29.py | 21 passed |
| tests/test_storyflow_writing_integration.py | 3 passed |
| handoff success/persistence | 有 |
| handoff provider failure | 本轮未见对应专项回归 |
| handoff worker crash/restart | 本轮未见对应专项回归 |
| handoff overlay reconciliation after failure | 有服务/测试 seam，未做完整浏览器 flow |

## 缺口

近期 handoff 仅用 success/persistence test 证明“任务被创建且参数落库”；未证明以下 P0-style contract：

- provider/config failure 不会错误完成；
- worker 中途停止后 checkpoint/retry 能继续；
- overlay failure 后父任务不会假装完成；
- Canon 不会在 review failure 下写入；
- author decision 可在重启后继续。

因此记录 NF-P1-006（验证闭环缺失），并在路线图中要求独立 failure/recovery acceptance。

## 判定

Continuous Writing implementation verdict：PARTIAL。老的 L27/L28/L29、phase8/9/12 合约本轮通过；新增 StoryFlow handoff 的故障、重启和恢复门尚未达到同等证据强度。
