# 快速入门 — Feature-019 KB Per-Category Lifecycle

**目标读者**: 实现者 / Reviewer / SRE
**前置条件**: Feature-018 已完成且迁移已升级至 0016

---

## 0. 阅读顺序

1. [spec.md](./spec.md) — 功能规范与澄清决议
2. [research.md](./research.md) — 7 项关键技术决策
3. [data-model.md](./data-model.md) — 6 个实体 schema 与迁移骨架
4. [contracts/](./contracts/) — 5 个 API yaml + error-codes.md
5. 本 quickstart

---

## 1. 核心心智模型

### 重构前（Feature-018 及之前）

```
tech_knowledge_bases（全表单 active）
├── version='1.0.0'  status=archived  action_types_covered=[forehand_attack]
├── version='1.1.0'  status=archived  action_types_covered=[forehand_attack, backhand_topspin]
└── version='1.2.0'  status=active    action_types_covered=[backhand_topspin]
                     ↑ 全局唯一 active；approve 任何新草稿会把它归档 → 误伤其它类别
```

### 重构后（Feature-019）

```
tech_knowledge_bases（每类别独立 active）
├── (forehand_attack,   1)  status=archived
├── (forehand_attack,   2)  status=archived
├── (forehand_attack,   3)  status=active      ← 正手攻球当前版
├── (backhand_topspin,  1)  status=archived
├── (backhand_topspin,  2)  status=active      ← 反手拉当前版，与正手攻球互不影响
└── (forehand_attack,   4)  status=draft       ← 待批
```

---

## 2. 本地开发环境准备

```bash
# 激活项目虚拟环境（章程附加约束）
source /opt/conda/envs/coaching/bin/activate

# 切到功能分支
git checkout 019-kb-per-category-lifecycle

# 数据库（未上线假设：可以自由清库重建）
alembic downgrade base                         # 清掉所有历史数据
alembic upgrade head                           # 升到 0017

# 或使用 system-init skill 一键清库：
# /skills system-init
```

---

## 3. 核心开发路径（建议 TDD 顺序）

### 阶段 A — 迁移与模型（基础）

1. 写 `src/db/migrations/versions/0017_kb_per_category_redesign.py`（上下两套）
2. 本地 `alembic upgrade head && alembic downgrade -1` 来回跑 3 次验证幂等
3. 更新 6 张表的 ORM 模型（`tech_knowledge_base.py` / `teaching_tip.py` / `expert_tech_point.py` / `analysis_task.py` / `reference_video.py` / `skill_execution.py` / `athlete_motion_analysis.py`）

### 阶段 B — 错误码与合约测试（TDD Red）

1. 在 `src/api/errors.py` 登记 4 个新 `ErrorCode`（见 contracts/error-codes.md）
2. 写 5 个合约测试 `tests/contract/test_kb_*.py`（期望失败——接口尚未实现）
3. 运行 `pytest tests/contract/ -v` 确认全部 Red（章程原则 II）

### 阶段 C — Service 层实现（TDD Green）

1. 重构 `knowledge_base_svc.py::approve_version(tech_category, version, approved_by, notes?)`
   - 行级锁 + partial unique index 双保险（研究 R3）
   - 联动 `teaching_tips` 归档/激活（研究 R5）
2. 重构 `tech_standard_builder.py::build(tech_category)`
   - 强制单类别、幂等检查（研究 R5+R7）
3. 新增 `teaching_tip_svc.py`
4. 微调 `kb_extraction_pipeline/step_executors/persist_kb.py`
   - 按 `expert_tech_points.action_type` 分组产出 N 条 KB 记录（每类别一条 draft）

### 阶段 D — Router 层组装

1. 重构 `knowledge_base.py` 路由：复合路径 `/{tech_category}/{version}`
2. 修改 `standards.py`：`build` 必填 `tech_category`
3. 修改 `extraction_jobs.py`：响应新增 `output_kbs`
4. 修改 `teaching_tips.py`：默认过滤 `status=active`

### 阶段 E — 单元测试补齐

1. `test_approve_version_branches.py` — 覆盖故事 1 的 6 条分支
2. `test_tech_standard_builder_per_category.py` — 故事 3 的 3 条验收场景
3. `test_teaching_tip_svc_lifecycle.py` — 故事 4 的 4 条验收场景

### 阶段 F — 集成测试 + 文档同步

1. `test_0017_migration_roundtrip.py` — 升/降级 3 次幂等
2. 启用 refresh-docs skill 同步：
   - `docs/business-workflow.md` § 4.2 单 active 措辞
   - `docs/business-workflow.md` § 7.4 错误码表加 4 行
   - `docs/architecture.md` 更新 KB 实体图

---

## 4. 本地手动冒烟（验收故事 1–5）

### 冒烟 1 — 按类别独立审批（故事 1）

```bash
# 启动 API + 5 Worker + Beat（参考 docs/architecture.md）
# 假设已有一条 extraction_job 产出了 forehand_attack v1 (draft) 与 backhand_topspin v1 (draft)

# 批准正手攻球 v1
curl -X POST http://localhost:8080/api/v1/knowledge-base/versions/forehand_attack/1/approve \
  -H "Content-Type: application/json" \
  -d '{"approved_by":"coach_zhang"}'

# 验证：列表里 forehand_attack v1 变 active；backhand_topspin v1 仍是 draft（不受影响）
curl "http://localhost:8080/api/v1/knowledge-base/versions?status=active"
curl "http://localhost:8080/api/v1/knowledge-base/versions?tech_category=backhand_topspin"
```

### 冒烟 2 — 追溯 KB ↔ Job（故事 2+5）

```bash
# 1. 列表随便拿一条 extraction_job_id
# 2. 反查该 job 详情，应能看到 output_kbs
curl http://localhost:8080/api/v1/extraction-jobs/<JOB_ID>
# 响应 data.output_kbs 非空，含 (forehand_attack, 1) 这类条目
```

### 冒烟 3 — 按类别构建 standard（故事 3）

```bash
# 正手攻球已有 active KB（上一步完成）
curl -X POST http://localhost:8080/api/v1/standards/build \
  -H "Content-Type: application/json" \
  -d '{"tech_category":"forehand_attack"}'

# 再来一次（指纹相同）→ 应返回 409 STANDARD_ALREADY_UP_TO_DATE
curl -X POST http://localhost:8080/api/v1/standards/build \
  -H "Content-Type: application/json" \
  -d '{"tech_category":"forehand_attack"}'
```

### 冒烟 4 — 教学提示联动（故事 4）

```bash
# KB approve 前：teaching_tips 里该类别 tips status=draft
curl "http://localhost:8080/api/v1/teaching-tips?tech_category=forehand_attack&include_status=draft"

# KB approve 后：默认请求（无 include_status）应返回已激活的 tips
curl "http://localhost:8080/api/v1/teaching-tips?tech_category=forehand_attack"
```

---

## 5. 常见问题

**Q: downgrade 会丢失数据吗？**
A: 会。系统未上线前提下接受丢失；生产环境接入前需追加回填脚本（不在本 Feature 范围）。

**Q: 如何处理并发 approve 同一类别？**
A: `FOR UPDATE` 行级锁 + `pg_advisory_xact_lock(hashtext(tech_category))` 命名空间锁双保险；最后还有 partial unique index 防双 active。详见 research.md R3。

**Q: 一次 extraction_job 能产出多条 KB 吗？**
A: 能。按 `expert_tech_points.action_type` 分组，每类别产出一条 KB 记录，共享同一 `extraction_job_id`。详见 data-model.md 实体 1。

**Q: teaching_tips 的 human 条目会被归档吗？**
A: 不会。`approve` 联动的归档 SQL 包含 `source_type='auto'` 过滤；`source_type='human'` 保留原状态（FR-024）。

---

## 6. 章程检查清单（合并前自检）

- [ ] 所有 API 使用 `SuccessEnvelope[T]` + `ok()/page()` 构造（不得手写 dict）
- [ ] 所有错误经 `AppException` 抛出（不得直抛 `HTTPException`）
- [ ] 4 个新 ErrorCode 登记到 `src/api/errors.py` 三张表 + `contracts/error-codes.md` + `docs/business-workflow.md § 7.4`
- [ ] 合约测试先于路由实现创建且可 Red（章程原则 II）
- [ ] 迁移 `upgrade/downgrade` 各跑 3 次可幂等
- [ ] `docs/business-workflow.md § 4.2 / § 7.4` 由 refresh-docs skill 同步
- [ ] `quickstart.md` 冒烟全部通过
- [ ] spec.md SC-001~SC-007 全部可测试且有对应测试覆盖

完成 ✅ 后方可合并到 master。
