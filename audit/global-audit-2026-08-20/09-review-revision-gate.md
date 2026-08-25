# Review / Revision Gate

## 正常 pipeline

src/pipeline/writing_pipeline.py 的质量门有真实判断：

- score 需达到阈值；
- effective blocking issues 必须为零；
- verdict 必须 pass；
- 未通过且仍有 revision budget 时进入 revision；
- 达到 max revisions 后进入 needs_author_decision，不会假装完成；
- 通过后 _create_commit 将 review_id 带入 StoryCommit；
- Canon acceptance 继续执行 version fence 与事实/状态写入。

## 官方 P0 合约

scripts/generate_progress.py --verify 本轮 exit 0，包含：

- review-gate.yaml / REVIEW-001；
- tests phase9 + phase12；
- 总体五项 P0 contract 均报告 VERIFIED。

这证明 protected contract suite；不证明内部所有 domain seam 都强制 review。

## 复现的 gate bypass

~~~text
create_story_commit(review_id=None, review_score=0, blocking_issues=0)
accept_story_commit(...)
=> REVIEW_ID=None
=> LOW_SCORE=0.0
=> ACCEPTED=True STATUS=accepted
~~~

代码位置：src/core/story_repository.py:1051-1075。实现中还保留了“legacy callers without a review remain a visible compatibility path”的兼容说明。

## 判断

| 路径 | 结果 |
|---|---|
| Studio/normal writing pipeline | IMPLEMENTED |
| phase9/phase12 acceptance | VERIFIED |
| arbitrary internal StoryRepository caller | PARTIAL |
| user-visible arbitrary commit endpoint | 未发现直接暴露 |

这不是当前普通 Studio writer 路径立即可点击的绕过，但它破坏了 StoryCommit 层“Canon 必须绑定 review”的强不变量。按 P1 处理；如果未来暴露该 domain seam，应升级为 P0。

## 其他 gate

- continuous-writing.yaml：failed quality gate 不得 false complete；focused L27-L29 suite 通过。
- review-gate.yaml：blocking review prevents passage；phase9/12 suite 通过。
- author override 是显式 decision，不是隐式自动放行。

## 判定

Review/revision gate 判定：PARTIAL。正常路径和受保护合约可信；review 绑定应在 accept_story_commit 层强制，或明确只允许受控 migration/legacy adapter 使用。
