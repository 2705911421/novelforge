# NovelForge Architecture V2：备份与恢复

## 备份

在 StoryCommit accepted 后创建可校验的逻辑备份清单：数据库一致性快照、涉及附件的 hash、schema version、生成时间与原因。用户可请求手动备份；同一 Book 的保留策略不得删除最后一个可验证备份。备份目录不与工作数据混用。

## 恢复

恢复先创建当前状态备份，再验证 manifest、hash、schema 兼容性和磁盘空间。恢复作为 Task 执行，停止同一 Book 的写任务，完成后运行数据库完整性检查和投影一致性检查。章节级恢复通过创建新的 ChapterVersion/StoryState 回放完成，不能直接重写不可变 commit 审计记录。

