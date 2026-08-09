# Phase 编号对齐记录

原始产品路线图的编号是唯一基线：Phase 0 为参考审计，Phase 1 为 Architecture + Data Model，Phase 2 为 Database + Story System。

早期工作树把数据库与 Story System 的实施规格命名为 `phase-01-database-story-system.md`。该文件保留为实施历史，不删除、不改写；其内容自 2026-08-07 起归属为**未关闭的 Phase 2**。新的规格文件使用规范编号，进度、验收和后续提交均以本记录为准。

| 规范编号 | 名称 | 现状 |
|---|---|---|
| 0 | Reference Reverse Engineering | 已建立审计与 clean-room 证据，后续按实际代码更新 |
| 1 | Architecture + Data Model | 架构和正式领域模型已归档；不包含生产写路径宣称 |
| 2 | Database + Story System | 实施中；迁移、StoryCommit、TaskRuntime、worker 与浏览器任务验收已有证据；Phase 3 已接管原生 Book/Chapter 写入 |
| 3 | Book + Chapter Core | 实施中；原生 Project/Book/Chapter、版本历史、乐观并发、状态机与 Review 版本关联已有证据；Diff/恢复、完整应用服务与浏览器状态流仍待完成 |
