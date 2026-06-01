# Quickstart: Feature 023 技术分类体系重构

**关联**: [plan.md](./plan.md) / [data-model.md](./data-model.md) / [research.md](./research.md)  
**目标读者**: 开发者本机演练 / SRE 部署执行 / 联调验收

> ⚠️ 本流程**不可回滚业务数据**（system-init TRUNCATE 后无法恢复）。生产环境执行前必须：
> 1. 与运营/产品确认零兼容窗口已达成共识
> 2. 备份 PostgreSQL（`pg_dump`）至安全位置
> 3. 通知所有客户端调用方接口字段变更（`tech_category` → 四级 + `action`）
>
> 生产执行窗口预估 ≤ 30 分钟（迁移 ≤ 2 分钟 + system-init ≤ 1 分钟 + 全量 COS 扫描首批入队 ≤ 5 分钟 + 健康检查 ≤ 10 分钟）；扫描后台异步进行，spec SC-006 要求 24h 内完成 1015+ 视频。

---

## 0. 前置检查（必做）

```bash
# 0.1 确认在功能分支
git rev-parse --abbrev-ref HEAD   # 应当输出 023-tech-classification-rebuild

# 0.2 确认当前迁移版本是 0021
/opt/conda/envs/coaching/bin/alembic current
# 期望: 0021_content_review_workflow (head)

# 0.3 确认 tech_actions 字典 seed CSV 已存在
test -f specs/023-tech-classification-rebuild/contracts/tech-actions-seed.csv && echo OK
test -f pp_book/pp_tech_classification.csv && echo OK

# 0.4 确认数据库为本地（非生产）
/opt/conda/envs/coaching/bin/python3.11 -c "
from src.config import get_settings
u = get_settings().database_url
assert 'localhost' in u or '127.0.0.1' in u, f'refuse non-local DB: {u}'
print('DB ok:', u)
"
```

---

## 1. 停服

```bash
# 停 5 个 Celery worker + Beat
pkill -f 'celery -A src.workers.celery_app worker'
pkill -f 'celery -A src.workers.celery_app beat'
sleep 2

# 停 API
pkill -f 'uvicorn src.api.main'
sleep 2

# 验证全部停止
ps aux | grep -E 'celery|uvicorn' | grep -v grep
# 期望: 仅返回 grep 自身行（即无任何 celery/uvicorn 残留）
```

---

## 2. 应用迁移（schema 重建）

```bash
# 2.1 升级到 0022
/opt/conda/envs/coaching/bin/alembic upgrade head
# 期望日志末尾:
#   INFO  [alembic.runtime.migration] Running upgrade 0021_content_review_workflow -> 0022_tech_taxonomy_rebuild

# 2.2 验证 tech_actions 字典已 seed 56 行
PGPASSWORD=password psql -h localhost -U postgres -d coaching_db -c "SELECT count(*) FROM tech_actions;"
# 期望: count=44

# 2.3 验证旧 tech_category 列已物理删除
PGPASSWORD=password psql -h localhost -U postgres -d coaching_db -c "
SELECT count(*) FROM information_schema.columns
WHERE table_name = 'coach_video_classifications' AND column_name = 'tech_category';
"
# 期望: count=0

# 2.4 验证 tech_knowledge_bases 新主键
PGPASSWORD=password psql -h localhost -U postgres -d coaching_db -c "
SELECT conname FROM pg_constraint
WHERE conrelid = 'tech_knowledge_bases'::regclass AND contype='p';
"
# 期望: pk_tech_kb_action_ver
```

---

## 3. 业务数据清场（system-init skill）

```bash
# 3.1 跑 system-init（已包含 task_channel_configs reseed）
PGPASSWORD=password psql -h localhost -U postgres -d coaching_db \
  -v ON_ERROR_STOP=1 \
  -f .codebuddy/skills/system-init/reset_business_data.sql

# 3.2 验证业务表已 TRUNCATE
PGPASSWORD=password psql -h localhost -U postgres -d coaching_db -c "
SELECT 'coach_video_classifications' AS tbl, count(*) AS rows FROM coach_video_classifications
UNION ALL SELECT 'expert_tech_points', count(*) FROM expert_tech_points
UNION ALL SELECT 'tech_knowledge_bases', count(*) FROM tech_knowledge_bases
UNION ALL SELECT 'analysis_tasks', count(*) FROM analysis_tasks
UNION ALL SELECT 'tech_actions（字典，应保留 44）', count(*) FROM tech_actions
UNION ALL SELECT 'task_channel_configs（reseed 应有 4-5 行）', count(*) FROM task_channel_configs;
"
# 期望:
#  business 表 = 0
#  tech_actions = 44
#  task_channel_configs >= 4
```

---

## 4. 启动 API + 5 Worker + Beat

```bash
# 4.1 API
: > /tmp/uvicorn.log
setsid /opt/conda/envs/coaching/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8080 \
  >> /tmp/uvicorn.log 2>&1 < /dev/null & disown
sleep 5
grep -E 'Application startup|ERROR|Traceback' /tmp/uvicorn.log | tail -5

# 4.2 Worker × 5（按现有项目规则脚本）
bash .codebuddy/skills/system-init/restart_workers.sh
# 或手动按项目规则 7 段 setsid 命令逐条启动

# 4.3 Beat
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app beat \
  --loglevel=info --schedule=/tmp/celerybeat-schedule \
  >> /tmp/celery_beat.log 2>&1 & disown

# 4.4 健康检查
curl -s http://localhost:8080/health
# 期望: {"status":"ok"}

curl -s http://localhost:8080/api/v1/admin/channels | python3 -m json.tool | head -30
# 期望: 4-5 通道全部 enabled=true
```

---

## 5. 触发全量 COS 扫描（重建数据）

```bash
# 5.1 触发扫描
SCAN_RESPONSE=$(curl -s -X POST http://localhost:8080/api/v1/classifications/scan)
echo $SCAN_RESPONSE
TASK_ID=$(echo $SCAN_RESPONSE | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")
echo "scan task_id=$TASK_ID"

# 5.2 轮询进度（每 30 秒一次，直到完成）
while true; do
  STATUS=$(curl -s "http://localhost:8080/api/v1/classifications/scan/$TASK_ID" | python3 -m json.tool)
  echo "$(date +%H:%M:%S) $STATUS"
  echo "$STATUS" | grep -q '"status": "success"' && break
  echo "$STATUS" | grep -q '"status": "failed"' && { echo "FAILED"; exit 1; }
  sleep 30
done
```

---

## 6. 验收（spec SC-001 / SC-002 / SC-006）

```bash
# 6.1 SC-001: 95% 以上记录的 action 字段非空（unclassified 视为已分类但精度不足，不计入分子）
PGPASSWORD=password psql -h localhost -U postgres -d coaching_db -c "
SELECT
  count(*) AS total,
  count(action) FILTER (WHERE action IS NOT NULL) AS has_action,
  count(action) FILTER (WHERE action IS NOT NULL AND action != 'unclassified') AS classified,
  ROUND(100.0 * count(action) FILTER (WHERE action IS NOT NULL) / NULLIF(count(*),0), 2) AS coverage_pct,
  ROUND(100.0 * count(action) FILTER (WHERE action IS NOT NULL AND action != 'unclassified') / NULLIF(count(*),0), 2) AS classified_pct
FROM coach_video_classifications;
"
# 期望: coverage_pct >= 95.0

# 6.2 SC-002: 抽样人工核验 100 条与基准对比（独立离线流程，输出至 docs/benchmarks/tech_classification_v2.md）
/opt/conda/envs/coaching/bin/python3.11 specs/023-tech-classification-rebuild/scripts/eval_v2_accuracy.py \
  --eval-set data/eval/tech_classification_v2_eval.csv \
  --output docs/benchmarks/tech_classification_v2.md
# 期望: accuracy >= 0.85

# 6.3 SC-006: 1015+ 视频在 24h 内完成（通过 6.1 的 total 与扫描任务时间对比）
PGPASSWORD=password psql -h localhost -U postgres -d coaching_db -c "
SELECT count(*) AS total, min(created_at) AS first, max(updated_at) AS last,
       (max(updated_at) - min(created_at)) AS duration
FROM coach_video_classifications;
"
# 期望: total >= 1015, duration < interval '24 hours'
```

---

## 7. 接口字段变更回归

```bash
# 7.1 GET /classifications 响应字段（确认新四级字段存在 + tech_category 已消失）
curl -s "http://localhost:8080/api/v1/classifications?page=1&page_size=3" | python3 -m json.tool
# 期望: data[*] 含 category_l1/l2/l3/action；不含 tech_category

# 7.2 GET /standards 按 action 查询
curl -s "http://localhost:8080/api/v1/standards?action=高吊弧圈球" | python3 -m json.tool
# 期望: 200 + data 中 action="高吊弧圈球" 的标准列表

# 7.3 GET /standards 旧参数已下线（404 / 422）
curl -s -o /dev/null -w "%{http_code}\n" \
  "http://localhost:8080/api/v1/standards?tech_category=forehand_topspin"
# 期望: 422（FastAPI 默认拒绝未声明参数）

# 7.4 字典违规校验
curl -s -X POST http://localhost:8080/api/v1/tasks \
  -H 'Content-Type: application/json' \
  -d '{"task_type":"kb_extraction","task_kwargs":{"action":"不存在的动作"}}' \
  | python3 -m json.tool
# 期望: 400 + error.code == "ACTION_DICTIONARY_VIOLATION"
```

---

## 8. 回滚剧本（紧急情况）

> 业务数据**不可回填**。回滚仅恢复 schema 结构。

```bash
# 8.1 停服
pkill -f 'celery|uvicorn'; sleep 2

# 8.2 alembic downgrade
/opt/conda/envs/coaching/bin/alembic downgrade -1
# 期望: Running downgrade 0022_tech_taxonomy_rebuild -> 0021_content_review_workflow

# 8.3 重新 system-init（业务表为空但 schema 已回到旧结构）
PGPASSWORD=password psql -h localhost -U postgres -d coaching_db \
  -v ON_ERROR_STOP=1 \
  -f .codebuddy/skills/system-init/reset_business_data.sql

# 8.4 重启服务（与 § 4 一致）

# 8.5 触发全量扫描重建（按旧 21 类体系）
curl -X POST http://localhost:8080/api/v1/classifications/scan
```

---

## 9. 故障排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `alembic upgrade` 报 FK 违反 | system-init 未先执行（业务表非空） | 先 § 3 system-init，再 § 2 迁移；正确顺序: stop → migrate → init |
| `tech_actions count=0` | seed 函数未读到 CSV | 检查 `pp_book/pp_tech_classification.csv` 文件 + 编码 UTF-8 |
| 全量扫描卡住 | LLM 兜底超时（Venus Proxy 抖动） | 检查 `/tmp/celery_classification_worker.log` 中 LLM 调用日志，必要时降级到关键词匹配（修改 prompt） |
| `ACTION_DICTIONARY_VIOLATION` 频繁触发 | LLM 返回不在字典的值 | 检查 prompt 中的 enum 列表是否完整；二次校验逻辑应将其降级为 unclassified 而非外抛 400 |
| 接口返回 `tech_category is undefined` | 客户端未更新 | 通知客户端按新契约调整（`category_l1/l2/l3/action`） |
