# Database / Migrations

## Schema 与运行参数

只读检查权威 projects/novelforge.db：

- journal_mode=wal；
- integrity_check=ok；
- 103 tables；
- migration max=45；
- connection-level foreign_keys=ON via Database.connect；
- busy timeout/WAL configured；
- no migration error swallowed in inspected runner。

## Migration runner

src/core/database.py 的 migration path：

1. 初始化前做 SQLite online backup；
2. 检查 migration checksum；
3. 使用 BEGIN IMMEDIATE；
4. apply migration；
5. insert checksum record；
6. commit；
7. exception 时 rollback/re-raise；
8. extension migrations 同样经过事务和 integrity checks。

phase1 persistence tests 覆盖 checksum tampering 与 online backup；全量测试通过。

## 数据恢复实测

本轮没有在权威 DB 执行 rebuild。通过 online backup 复制到 ignored disposable fixture：

~~~text
BEFORE: accepted_commits=12, narrative_events=0, narrative_memory=0
CALL: StoryRepository(copy).rebuild_all(book_id)
RESULT: rebuilt
ACCEPTED: 12
MATERIALIZED: story_facts=762, narrative_memory=762
AFTER: narrative_events=12, narrative_memory=762
~~~

该结果证明 rebuild 是可用恢复 seam，也证明当前权威 DB 的 derived projections 不是完整快照。StoryRepository.__init__ 不自动 replay/rebuild，Studio lifespan 也没有发现启动回填调用，故记录 NF-P1-005。

## Claim → Evidence → Verification → Result

| Claim | Evidence | Verification | Result |
|---|---|---|---|
| migration 原子可回滚 | transaction/rollback/re-raise | phase1 tests + source | IMPLEMENTED |
| backup 保护 WAL DB | SQLite online backup + integrity check | isolated backup test | IMPLEMENTED |
| latest schema 已应用 | migration max 45 / 103 tables | read-only query | IMPLEMENTED |
| current projections complete | events/memory counts | authoritative DB query | PARTIAL |
| recovery can reconstruct | rebuild_all | backup copy result | IMPLEMENTED |
| startup automatically repairs projections | no call in init/lifespan | source inspection | NOT_IMPLEMENTED |

## 安全边界

- WAL sidecars 未被手动删除；
- 没有复制活动中的 .db 文件作为普通文件替代 online backup；
- 权威 DB 未被审计脚本修改；
- fixture/backup 都落在 ignored output。

## 判定

DB/migration implementation verdict：PARTIAL。迁移机制和备份验证较强；数据 projection freshness 和启动恢复策略必须补齐后，才能把数据库状态称为完整可交付。
