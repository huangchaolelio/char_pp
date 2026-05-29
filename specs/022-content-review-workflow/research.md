# 阶段 0 研究: Feature-022 业务流程四阶段化 + 内容准备阶段引入审核门

**关联**: [plan.md](./plan.md) | [spec.md](./spec.md)
**日期**: 2026-05-28

> 本文档对 plan.md 阶段 0 表格中识别的 7 项决策做完整说明：每项采用 **Decision / Rationale / Alternatives considered** 三段格式。所有 NEEDS CLARIFICATION 已通过 spec.md `Clarifications` 段落（Q1–Q5）解决，本文档仅承接其中由 plan 推导出的下游技术决策。

---

## R1：`business_phase_enum` 扩值方式

**Decision**：在迁移 `0021_content_review_workflow.py` 的 `upgrade()` 起始处使用 PostgreSQL 原生 `ALTER TYPE business_phase_enum ADD VALUE IF NOT EXISTS 'CONTENT_PREP' BEFORE 'TRAINING'`，并立即提交（不在事务内）。回填 SQL：

```sql
UPDATE analysis_tasks SET business_phase = 'CONTENT_PREP'
WHERE task_type IN ('video_classification', 'video_preprocessing', 'video_curation');

UPDATE video_preprocessing_jobs SET business_phase = 'CONTENT_PREP';
-- video_curation_jobs（Feature-021）若已加 business_phase 列也一并 UPDATE
```

`extract_kb` / KB 版本激活继续保留 `TRAINING`；学员诊断保留 `INFERENCE`；技术标准构建保留 `STANDARDIZATION`。

**Rationale**：
- PostgreSQL 14+ 支持 `ALTER TYPE ... ADD VALUE` 即时生效，无需重建 enum；与项目主版本兼容
- `BEFORE 'TRAINING'` 使枚举的逻辑顺序与业务时序一致（CONTENT_PREP → TRAINING → STANDARDIZATION → INFERENCE），方便 `ORDER BY business_phase` 直接得到流程顺序
- `IF NOT EXISTS` 保证迁移幂等（章程偏好幂等迁移）

**Alternatives considered**：
- 用字符串列代替 enum：被原则 IV 拒绝（已有 enum 不应回退）
- 在 ORM 层维护 enum 顺序：被否决（DB 层强约束更可靠）
- 不迁移既有数据、只对新行写 `CONTENT_PREP`：被否决，将导致阶段视图统计错乱（违反 SC-001）

---

## R2：`coach_video_classifications` 新增列 vs 子表

**Decision**：**直接在 `coach_video_classifications` 上加 4 列**（`review_state` / `review_version` / `last_decision_id` / `pending_since`），不另建 `content_review_items` 子表。

**Rationale**：
- 澄清 Q1 已确定审核粒度 = 整段视频条目（与该表行 1:1）
- 子表会导致每次列表查询都要 `LEFT JOIN`，违反 FR-017 性能 SLO（P95 < 500 ms）
- 该表已有 `kb_extracted` / `preprocessed` / `low_quality` 等多个状态字段，新增 4 列与现有命名风格一致
- 决策留痕仍走独立的 `content_review_decisions` 表（多版本历史无法挂载在主表上），主表只保 "最近一次" 引用

**Alternatives considered**：
- 子表 `content_review_items`：被否决（增加 join 成本，违反 SLO）
- 仅 `review_state` 一列 + 决策表自接：缺 `pending_since`、`review_version`，无法支持积压告警（FR-016）和乐观锁（隐含约束）
- 用 JSONB 列承载所有审核字段：被否决（与项目其他表的关系模式不一致；索引复杂度高）

---

## R3：审核决策留痕表是否复用 `pipeline_steps`

**Decision**：**独立建表 `content_review_decisions`**。

**Rationale**：
- `pipeline_steps`（Feature-014）记录 KB 抽取 DAG 的子步骤执行轨迹，颗粒度="步骤实例"（包含输入/输出工件、错误信息、重试次数等大量字段）
- 审核决策颗粒度=人工动作，字段集（`reviewer_id` / `decision` / `reason_code` / `note`）完全不同
- 复用会导致 `pipeline_steps` 字段膨胀和语义污染，违反 YAGNI 与"单表单职责"
- 独立表方便后续加索引（如 `(reviewer_id, decided_at)` 做人均吞吐统计）

**Alternatives considered**：
- 复用 `pipeline_steps`：见上方否决理由
- 不留决策表，仅在主表存最新决策的几个字段：被否决，无法满足 FR-010"持久化每一次审核决策"和 FR-015 时间窗统计

---

## R4：审核门绕过开关的承载位置

**Decision**：双层承载：
1. **环境变量层**（启动级开关）：`settings.kb_extraction_bypass_review_gate: bool = False`，需重启 API 才能生效；用作"应急熔断"
2. **DB 行级热配置**（运行时开关）：在 `task_channel_configs`（已有表）新增一行 `task_type='content_review_gate'`，承载 `enabled: bool` + `last_toggled_at` + `last_toggled_by`，30 秒内热生效；用作"日常切换"

切换接口：`PATCH /api/v1/admin/review-gate`（鉴权 `X-Admin-Token`），写 DB 同时记审计日志。

**Rationale**：
- 章程原则 X "运行时参数"杠杆要求"30 秒内生效、无需重启"，DB 行级热配置满足
- 环境变量层兜底防止 DB 不可用时无法熔断
- 复用 `task_channel_configs` 避免新增"配置表"，与 Feature-013 / 018 体系一致

**Alternatives considered**：
- 只用环境变量：违反"30 秒生效"要求
- 新建独立 `feature_flags` 表：被原则 IV 否决（YAGNI，复用现有表已够用）
- 用 Redis key：被否决（DB 持久化更可靠，且 task_channel_configs 已是配置体系的单一事实来源）

---

## R5：审核员鉴权（项目无登录体系）的最简方案

**Decision**：
- HTTP 鉴权层：所有 `/api/v1/content-reviews/**` 写接口走 `X-Admin-Token` 头，与现有 admin 路由一致
- 身份标识层：在请求体里要求传 `reviewer_id`（VARCHAR(64)）；POST 决策接口同时要求 `X-Reviewer-Id` header 和请求体 `reviewer_id` 一致（防止伪造），不一致返回 400 `INVALID_REVIEWER_IDENTITY`
- 审计落库：`content_review_decisions.reviewer_id` 直接存请求传入的字符串值，不做白名单校验（澄清 Q2 决议：单一 reviewer 角色，admin 为超集，无独立账号体系）

**Rationale**：
- 项目当前完全无登录态，新增账号系统是独立 Feature 范围（违反 YAGNI）
- `X-Admin-Token` 已是项目 admin 操作的事实标准，复用减少决策点
- `reviewer_id` 字符串自由命名，由运营约定（如工号），未来若引入账号系统可平滑切换

**Alternatives considered**：
- 引入完整的 OAuth2 / JWT：违反 YAGNI 和测试阶段简洁原则
- 完全不要 `reviewer_id`，只记 `decided_at`：违反 FR-010（持久化审核员标识）
- 仅靠 `X-Reviewer-Id` header（不要请求体重复）：被否决（决策审计需要从请求体重建，header 在日志聚合中可能被脱敏）

---

## R6：重洗后审核失效的触发点

**Decision**：在 `src/services/curation/curation_service.py`（已有）的"清洗作业 success 落库"回调链尾部，调用新增 `src/services/content_review/stale_handler.py::mark_review_stale_after_recurate(cvclf_id)`：
- 若 `coach_video_classifications.review_state == 'approved'` → 设为 `'stale'`、`review_version += 1`、`pending_since = now()`
- 若 `review_state == 'pending_review'` → 不动（仍是待审核）
- 若 `review_state == 'rejected'` → 不动（已拒绝条目重洗后保持拒绝；若运营要重新审核需手工 reset）
- 在 `content_review_decisions` 上批量写 `superseded_at = now()` 给所有非 superseded 行

**Rationale**：
- 与 Feature-021 清洗回调同点对接，避免引入新的事件总线
- 状态转换显式且幂等（多次重洗只产生一次状态推进）
- 不级联中止已运行 `extract_kb` 任务（澄清 Q3 决议：FR-011a）

**Alternatives considered**：
- 用 PostgreSQL trigger 自动维护：被否决（trigger 难以单元测试；与项目"业务逻辑归 services 层"原则冲突）
- 异步 Celery 任务延迟处理：被否决（增加状态不一致窗口；同步处理足够轻量）
- 在 KB 抽取入口"懒检查"：被否决（无法支持工作台正确显示 stale 状态）

---

## R7：审核统计接口的 SQL 形态

**Decision**：`GET /api/v1/content-reviews/stats?from=...&to=...&group_by=reviewer|reason|day` 的 SQL 走如下形态：

```sql
-- 总量与通过率
SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE decision = 'approved') AS approved,
  COUNT(*) FILTER (WHERE decision = 'rejected') AS rejected
FROM content_review_decisions
WHERE decided_at >= :from AND decided_at < :to;

-- 平均时延（pending_since → decided_at）
SELECT AVG(EXTRACT(EPOCH FROM (d.decided_at - cvc.pending_since))) AS avg_latency_seconds
FROM content_review_decisions d
JOIN coach_video_classifications cvc ON cvc.id = d.cvclf_id
WHERE d.decided_at >= :from AND d.decided_at < :to;

-- 人均吞吐
SELECT reviewer_id, COUNT(*) AS decisions
FROM content_review_decisions
WHERE decided_at >= :from AND decided_at < :to
GROUP BY reviewer_id;
```

辅以索引 `idx_crd_decided_at` ON `content_review_decisions(decided_at)` 与 `idx_crd_reviewer_decided` ON `(reviewer_id, decided_at)`。

**Rationale**：
- 时间窗 + 索引扫描是 PG 上最高效的统计形态（O(log N + matched_rows)）
- `FILTER (WHERE ...)` 是 PG 9.4+ 标准语法，避免多次扫表
- 对 100 万行决策表，30 天窗口预期 P95 < 200 ms

**Alternatives considered**：
- 物化视图按天预聚合：被原则 IV 否决（YAGNI，当前规模无需）
- 实时维护 Redis counter：被否决（一致性维护成本高）
- 直接扫主表：被否决（主表行少但每行尺寸大，不如决策表紧凑）

---

## 总结

阶段 0 的 7 项研究决策已覆盖：
- **数据模型**（R1, R2, R3）：扩 enum + 加列 + 独立决策表
- **运行时配置**（R4）：双层开关，复用既有 `task_channel_configs`
- **集成模式**（R5, R6）：复用 `X-Admin-Token`；在清洗 success 回调插入 stale 处理
- **性能**（R7）：索引 + `FILTER` 子句

无遗留 `NEEDS CLARIFICATION`。可进入阶段 1 设计。
