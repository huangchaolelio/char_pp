# Migration 0020 Smoke Test Notes

**Feature**: 021-video-content-curation
**Migration**: `src/db/migrations/versions/0020_video_content_curation.py`
**Revises**: `0019` → **Revision**: `0020`

---

## 静态链路校验（已通过，不依赖 DB）

```
$ alembic heads → ['0020']
$ alembic history (top 3) → ('0020','0019') → ('0019','0018') → ('0018','0017')
```

**结论**：alembic ScriptDirectory 正确将 0020 识别为唯一 head，down_revision 链路无断点；模块语法正确，可被 Alembic 解析装载。

---

## 实际数据冒烟（需本地 PostgreSQL，待运行）

执行环境：本仓库当次 sandbox 内 PostgreSQL 服务未启动（127.0.0.1:5432 拒绝连接）。在具备 DB 的环境复跑：

```bash
cd /data/charhuang/char_ai_coding/charhuang_pp_cn
# 1. 正向迁移
/opt/conda/envs/coaching/bin/alembic upgrade head

# 2. 校验表 / 列 / 索引 / ENUM / 默认配置已落地
psql "$DATABASE_URL" -c "\d video_curation_jobs"
psql "$DATABASE_URL" -c "\d video_curation_segment_results"
psql "$DATABASE_URL" -c "\d coach_video_classifications" | grep -E 'last_curation_job_id|low_quality|kb_stale_after_override'
psql "$DATABASE_URL" -c "SELECT enum_range(NULL::task_type_enum);"
psql "$DATABASE_URL" -c "SELECT * FROM task_channel_configs WHERE task_type = 'video_curation';"

# 3. 反向迁移
/opt/conda/envs/coaching/bin/alembic downgrade -1

# 4. 校验表 / 索引 / 列 / 配置已被清理（ENUM 值保留，符合预期）
psql "$DATABASE_URL" -c "\d video_curation_jobs"      # ⇒ Did not find any relation
psql "$DATABASE_URL" -c "\d coach_video_classifications" | grep -E 'last_curation_job_id'  # ⇒ 应为空
psql "$DATABASE_URL" -c "SELECT * FROM task_channel_configs WHERE task_type = 'video_curation';"  # ⇒ 0 行
psql "$DATABASE_URL" -c "SELECT enum_range(NULL::task_type_enum);"  # ⇒ 仍含 'video_curation'（PostgreSQL 不支持 DROP VALUE）

# 5. 重新正向，确认幂等
/opt/conda/envs/coaching/bin/alembic upgrade head
```

预期输出：

- 步骤 1：`Running upgrade 0019 -> 0020, Feature-021 — 视频内容清洗与有效片段筛选规范.`
- 步骤 2：两张新表 + 3 列 + ENUM 含 `video_curation` + `task_channel_configs` 默认行
- 步骤 3：`Running downgrade 0020 -> 0019`
- 步骤 4：表与列已删；ENUM 值保留（与 0018 / 0019 一致策略）；`task_channel_configs` 行已删
- 步骤 5：再次 success，幂等

---

## 已知不可逆点（已在迁移文件 docstring + 本备忘录登记）

- `task_type_enum` 的 `'video_curation'` 值在 downgrade 中**保留不删**（PostgreSQL 不支持事务内 `ALTER TYPE ... DROP VALUE`；与 0018 / 0019 一致）
- `coach_video_classifications.kb_stale_after_override` 是 `NOT NULL DEFAULT FALSE` 列；既有行迁移时被回填为 false，downgrade 直接 DROP 列，无数据风险
