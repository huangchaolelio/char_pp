# Quickstart: 处理流程规范化（Workflow Standardization） — Feature-018

**目标读者**: 研发（新 Feature 作者）、SRE（故障排查）、运营（了解新总览接口）
**预计上手时间**: 15 分钟

---

## 0. 前置

```bash
# 激活项目虚拟环境（章程附加约束「Python 环境隔离」）
export PYBIN=/opt/conda/envs/coaching/bin/python3.11

# 确保 Alembic 已升级到本 Feature 的迁移
alembic upgrade head   # 目标 revision: 0016_business_phase_step

# 启动 API（无热重载）
pkill -f "uvicorn src.api.main" && \
  setsid $PYBIN -m uvicorn src.api.main:app --host 0.0.0.0 --port 8080 \
  >> /tmp/uvicorn.log 2>&1 &
```

---

## 1. 验证业务阶段字段已落盘

```bash
psql -c "
  SELECT business_phase, business_step, count(*)
  FROM analysis_tasks
  GROUP BY 1, 2
  ORDER BY 1, 2;
"
```

预期输出（NULL 率 = 0%）:
```
 business_phase |   business_step    | count
----------------+--------------------+-------
 INFERENCE      | diagnose_athlete   |    14
 TRAINING       | classify_video     |    21
 TRAINING       | extract_kb         |    17
 TRAINING       | preprocess_video   |    28
 TRAINING       | scan_cos_videos    |     3
```

如有 NULL 行 ⇒ 迁移回填未正确执行，立刻 `alembic downgrade -1` 回滚并复核 `0016_business_phase_step.py::upgrade`。

---

## 2. 调用业务总览接口

### 完整档（≤ 100 万行）

```bash
curl -s http://localhost:8080/api/v1/business-workflow/overview | jq .
```

预期:
```json
{
  "success": true,
  "data": {
    "TRAINING": { "phase": "TRAINING", "steps": { ... } },
    "STANDARDIZATION": { "phase": "STANDARDIZATION", "steps": { ... } },
    "INFERENCE": { "phase": "INFERENCE", "steps": { ... } }
  },
  "meta": {
    "generated_at": "2026-04-30T12:51:20+08:00",
    "window_hours": 24,
    "degraded": false
  }
}
```

### 自定义窗口

```bash
curl -s "http://localhost:8080/api/v1/business-workflow/overview?window_hours=1" | jq '.meta'
```

### 越界触发 400

```bash
curl -s "http://localhost:8080/api/v1/business-workflow/overview?window_hours=200" | jq '.error'
# { "code": "INVALID_ENUM_VALUE", "message": "...", "details": { "field": "window_hours", "value": 200, "allowed": ["1..168"] } }
```

---

## 3. 按阶段/步骤筛选既有列表接口

```bash
# 训练阶段的抽取任务
curl -s "http://localhost:8080/api/v1/tasks?business_phase=TRAINING&business_step=extract_kb&page=1&page_size=20" | jq '.meta'

# 诊断阶段所有任务
curl -s "http://localhost:8080/api/v1/tasks?business_phase=INFERENCE" | jq '.meta.total'

# 语义矛盾 ⇒ 400 INVALID_PHASE_STEP_COMBO
curl -s "http://localhost:8080/api/v1/tasks?business_phase=INFERENCE&task_type=kb_extraction" | jq '.error'
```

---

## 4. 调用优化杠杆台账

```bash
export ADMIN_TOKEN=$(grep '^ADMIN_RESET_TOKEN=' .env | cut -d= -f2)

# 完整台账
curl -s -H "X-Admin-Token: $ADMIN_TOKEN" http://localhost:8080/api/v1/admin/levers | jq .

# 按阶段过滤
curl -s -H "X-Admin-Token: $ADMIN_TOKEN" \
  "http://localhost:8080/api/v1/admin/levers?phase=INFERENCE" | jq '.data.algorithm_models[] | .key'
```

**预期敏感键行为**:
- `VENUS_TOKEN` / `OPENAI_API_KEY` / `COS_SECRET_KEY` / `POSTGRES_PASSWORD` → 仅返回 `is_configured: true`，不含 `current_value`

**鉴权失败示例**:
```bash
curl -s http://localhost:8080/api/v1/admin/levers | jq '.error'
# { "code": "ADMIN_TOKEN_INVALID", ... }
```

---

## 5. 本地运行漂移扫描

### 全量扫描（默认模式）

```bash
$PYBIN -m scripts.audit.workflow_drift --full
# 预期: exit 0，stdout 打印 "OK: scanned=12, mode=full"
```

### 仅扫描本分支变更项（研发日常场景）

```bash
$PYBIN -m scripts.audit.workflow_drift --changed-only --commit-range=origin/master...HEAD
# 预期: exit 0；若无命中打印 "scope: changed-only, no target in diff"
```

### 模拟一次漂移（demo 用）

```bash
# 临时在错误码总集添加一个未在文档的 code
echo 'WHISPER_GPU_OOM = "WHISPER_GPU_OOM"' >> /tmp/pretend_error_codes.py
# 手动把 ALL_ERROR_CODES 包含进去，再跑扫描
$PYBIN -m scripts.audit.workflow_drift --full
# 预期: exit 1，stdout:
#   DRIFT: error_code_prefix WHISPER_GPU_OOM code_side=present doc_side=missing
#   SUMMARY: drift_count=1
```

---

## 6. 本地运行 Spec 合规扫描

```bash
$PYBIN -m scripts.audit.spec_compliance --full
# 预期: exit 0；specs/001-*/ ~ specs/017-*/ 已登记到 .scan-exclude.yml，只扫 specs/018+
```

## 7. 新建 Feature 时的 checklist

为了让 `spec_compliance.py` 扫描通过，新 Feature 的 `spec.md` MUST 含「业务阶段映射」小段，六项齐全：
- **所属阶段**: `TRAINING` / `STANDARDIZATION` / `INFERENCE`（跨阶段 ⇒ 拆多个用户故事）
- **所属步骤**: 八步骤之一（新扩展先走 `/speckit.constitution` + 业务流程文档更新）
- **DoD 引用**: `docs/business-workflow.md § 2` 对应行
- **可观测锚点**: `docs/business-workflow.md § 7` 对应子节
- **章程级约束影响**: 列出影响的章程级约束（队列 / 状态机 / 错误码等）
- **回滚剧本**: 引用 `docs/business-workflow.md § 10` 或提出新剧本

---

## 8. 故障注入：触发 `PHASE_STEP_UNMAPPED`

```python
# 在 Python shell 或单测中
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.analysis_task import AnalysisTask

# 构造一个非法的 task_type（需要 bypass TaskType Enum 的赋值校验）
task = AnalysisTask.__new__(AnalysisTask)
object.__setattr__(task, "task_type", "totally_new_type")  # 非 TaskType 枚举
session.add(task)
await session.flush()
# 预期: sqlalchemy.exc.StatementError 包装 ValueError("PHASE_STEP_UNMAPPED: unknown task_type='totally_new_type'")
```

服务层若捕获到该 `ValueError`，MUST 转为 `AppException(PHASE_STEP_UNMAPPED)` → 500。

---

## 9. 回滚路径

```bash
# 回退本 Feature 的迁移（删列 + 删 enum type，无业务数据损失）
alembic downgrade -1

# 下线新接口（临时应急）
# 注释掉 src/api/main.py 中的:
#   app.include_router(business_workflow.router, prefix="/api/v1")
# 重启 API 即可（/admin/levers 随 admin router 共存亡，暂不单独下线）
```

---

## 10. 常见问题

**Q: 扫描脚本在 CI 报 `DRIFT: xxx` 但文档我已经改了，为什么还是失败？**
A: 两层闸门独立运行。先在本地跑 `--changed-only`（研发日常）确认无差异，再 push；master 合并前还会再跑一次 `--full`。若仍失败，检查文档改动是否命中脚本 anchored-section（§ 7.4、§ 5.1 等）的锚点标题。

**Q: 我的 Feature 不涉及业务流程（例如改 CI 配置），还需要写「业务阶段映射」吗？**
A: 需要。填写理由："本 Feature 不归属任一阶段（治理/工具层），不修改章程级约束"。`spec_compliance.py` 只检查小段存在性，不校验每项是否为非空。

**Q: 新加杠杆键为什么先要改 YAML 再改代码？**
A: FR-014 要求杠杆入口集中登记，防止"调参入口失控"。YAML 是 fail-fast 的单一事实来源，漏登记 ⇒ 漂移扫描阻断合并。
