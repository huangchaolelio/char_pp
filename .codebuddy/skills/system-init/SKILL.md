---
name: system-init
description: 系统初始化：清理所有业务数据并重建配置表 seed（task_channel_configs），用于环境重置 / 压测后清场 / 联调清理。不删 schema、不动迁移、不清 alembic_version。
allowed-tools: Read, Bash, Grep
context: fork
---

## 触发时机

用户明确要求 **"系统初始化 / 清空业务数据 / 环境重置 / 清库"** 时调用。典型语句：
- "清空数据库数据"
- "系统初始化"
- "清理所有业务数据重新跑"
- "把环境重置一下"

> ⚠️ **本操作不可回滚**。执行前必须向用户二次确认，明确告知"所有业务表（分类、任务、知识库、视频、教练等）将被清空，仅保留 schema 与 alembic_version；task_channel_configs 将重建为迁移 seed 默认值"。

## 目录下的配套脚本

- `reset_business_data.sql` — 一键幂等 SQL 脚本：TRUNCATE 全部业务表 + 重播 `task_channel_configs` seed。所有动作跑在同一事务里，任何一步失败全部回滚。

## 执行前校验（必须做，按顺序）

1. **环境护栏**：确认当前不是生产库
   ```bash
   /opt/conda/envs/coaching/bin/python3.11 -c "from src.config import get_settings; u=get_settings().database_url; assert 'localhost' in u or '127.0.0.1' in u, f'refuse to wipe non-local DB: {u}'; print('DB ok:', u)"
   ```
   若 assert 失败 → 终止并报告，要求用户显式确认放行

2. **迁移版本校验**：确认 `alembic_version` 存在且与 `alembic heads` 一致
   ```bash
   /opt/conda/envs/coaching/bin/alembic current | head -5
   ```

3. **表清单一致性校验**：用 `information_schema` 列出 public schema 的表，与 `reset_business_data.sql` 里 TRUNCATE 清单比对；如果数据库实际表多于脚本覆盖的清单，**中止** 并提示用户"检测到未登记的新表 <name>，请先更新 system-init skill 的 SQL 清单"
   ```bash
   /opt/conda/envs/coaching/bin/python3.11 - <<'PY'
   import asyncio, asyncpg, re, pathlib
   SQL = pathlib.Path('.codebuddy/skills/system-init/reset_business_data.sql').read_text()
   covered = set(re.findall(r'TRUNCATE TABLE ([a-z_]+)', SQL))
   async def m():
       c = await asyncpg.connect('postgresql://postgres:password@localhost:5432/coaching_db')
       rows = await c.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
       await c.close()
       actual = {r['tablename'] for r in rows} - {'alembic_version'}
       missing = actual - covered
       if missing:
           raise SystemExit(f'未登记的表: {missing}；请更新 SKILL 的 SQL 清单')
       print('表清单一致，覆盖 %d 张业务表' % len(covered))
   asyncio.run(m())
   PY
   ```

## 执行步骤

1. **提醒 + 展示将清理/保留清单**（基于 SQL 内容文案化），等待用户确认
2. **停止可能写入的 Worker**（可选，推荐）：避免事务期间 Worker 写入产生孤儿数据
   ```bash
   pkill -f 'celery -A src.workers.celery_app worker' 2>/dev/null; sleep 2
   ```
3. **执行重置 SQL**（单事务）
   ```bash
   PGPASSWORD=password psql -h localhost -U postgres -d coaching_db \
     -v ON_ERROR_STOP=1 \
     -f .codebuddy/skills/system-init/reset_business_data.sql
   ```
4. **重启 API**（清除 `task_channel_service` 的 30 秒 TTL 缓存，避免提交任务时读到旧快照）
   ```bash
   pkill -f 'uvicorn src.api.main' 2>/dev/null; sleep 2
   : > /tmp/uvicorn.log
   setsid /opt/conda/envs/coaching/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8080 \
     >> /tmp/uvicorn.log 2>&1 < /dev/null & disown
   sleep 5
   grep -E 'Application startup|ERROR|Traceback' /tmp/uvicorn.log | tail -10
   ```
5. **重启 Worker**（如果步骤 2 停了）：按 `.codebuddy/skills/system-init/restart_workers.sh` 脚本统一拉起 5 个 Worker
6. **健康校验**（全部应通过才算成功）
   - 所有业务表 `count(*) == 0`
   - `task_channel_configs` 4 行齐全，值与迁移 seed 一致
   - `GET /health` 返回 `{"status":"ok"}`
   - `GET /api/v1/admin/channels` 4 通道 `enabled=true`

## 不应清理的对象（保留清单）

| 对象 | 类型 | 原因 |
|------|------|------|
| `alembic_version` | 表 | 迁移版本追踪，清了会让 alembic 以为是空库 |
| 所有枚举类型（`task_status_enum`、`task_type_enum` 等） | PG TYPE | schema 层对象，TRUNCATE 不涉及 |
| 所有索引、外键、触发器 | DDL | 仅清数据，不改 DDL |
| COS 存储桶里的对象 | 远端 | 不在本 skill 职责范围；如需连带清理，另起单独脚本并显式确认 |

## 清理后的下一步（由用户决定，不自动触发）

- 重新扫描 COS：`POST /api/v1/classifications/scan` → 填充 `coaches` / `coach_video_classifications`
- 重新提交预处理 / KB 提取 / 诊断任务

## 变更维护

> 每次新增业务表（迁移 `NNNN_*.py` 里 `create_table`），必须同步：
> 1. 更新 `reset_business_data.sql` 的 TRUNCATE 清单
> 2. 如果新表带 seed INSERT，更新 SQL 的 "配置表 seed" 段
> 3. 在 PR 里明确标注 "system-init skill 已对齐"
