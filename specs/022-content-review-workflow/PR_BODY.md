## Feature-022 · 业务流程四阶段化 + 内容准备阶段引入审核门

> 本文件为 PR 描述草稿，便于在 GitHub 网页一键创建 PR 时复制粘贴。
> 也作为合并完成前的功能交付摘要留档；合并完成后可保留作历史索引。

**分支**：`022-content-review-workflow` → `master`
**Commits**：3 个 / **49 文件** / **+8812 −103**

---

### 🎯 一句话目标

把业务流程从**三阶段**升级为**四阶段**（`CONTENT_PREP` / `TRAINING` / `STANDARDIZATION` / `INFERENCE`），并在内容准备阶段末端（`curate_segments` 之后）引入**内容审核门**作为最终判据，阻断未经审核或审核拒绝的素材进入下游 KB 提取流程。

---

### 📦 交付清单

| 维度 | 内容 |
|---|---|
| **数据库迁移** | `0021_content_review_workflow.py`（cvclf 表新增 5 列 + 新建 `content_review_decisions` 表 + 4 个索引）|
| **核心模型** | `ContentReviewDecision`、`coach_video_classifications.review_state` 状态机（4 状态：`pending_review` / `approved` / `rejected` / `stale`）|
| **API 路由** | `POST /api/v1/content-reviews/{cvclf_id}/decisions`（人工决策，乐观锁）、`GET /api/v1/content-reviews`（列表 + 统计）、`PATCH /api/v1/admin/review-gate`（30s 热开关）|
| **业务阶段** | `BusinessPhase` enum 扩展加入 `CONTENT_PREP`；`video_preprocessing_jobs` 从 `TRAINING` 重归属至 `CONTENT_PREP` |
| **审核门两层闸** | ① 路由层：`POST /tasks/kb-extraction` 提交时按 `review_state` 三态拒绝（409 + `CONTENT_NOT_REVIEWED` / `CONTENT_REVIEW_REJECTED` / `CONTENT_REVIEW_STALE`）；② 渠道级 bypass（admin 热开关）|
| **积压告警** | `cleanup_pending_backlog` beat 每小时巡检 `pending_since < now() - review_pending_red_line_hours`（默认 24h），命中即写 ERROR 级结构化日志（不阻塞业务）|

---

### ✅ 测试覆盖

| 类型 | 文件 | 用例数 |
|---|---|---|
| Contract | `tests/contract/test_022_content_reviews_contract.py` | 9 |
| Unit (review service) | `tests/unit/test_022_review_service.py` | 27 |
| Unit (phase/step hook) | `tests/unit/models/test_phase_step_hook.py` 等 | 18 |
| Integration | `tests/integration/test_022_*.py` (6 文件) | 24 |
| **小计** | — | **78 passed in 7.02s** ✅ |

---

### 🔬 真实数据端到端验证

- **样本**：孙浩泓 / `backhand_attack` 类目真实视频
- **路径**：扫描 → 预处理 → 分类 → 精选 → **审核门（自动审批通过）** → KB 提取 → 完成
- **结果**：✅ 全流程贯通，无遗漏阶段，状态机四态切换符合预期

---

### 🛠️ Commit 链

```
0f761cc  docs(022):   refresh-docs 反映激进收尾 + 修正与代码漂移的错字
dd6f150  fix(022):    激进收尾 — 索引命名对齐 + tz-aware 兼容 + 阶段矩阵测试同步
5ed8ae8  feat(022):   业务流程四阶段化 + 内容准备阶段引入审核门
```

#### 激进收尾（`dd6f150`）三处修复

1. **索引命名对齐**：`coach_video_classifications` 上 4 个索引名（`idx_cvclf_review_state` / `idx_cvclf_pending_since` / `idx_cvclf_review_version` / `idx_cvclf_last_decision_id`）从迁移端重命名，模型端 `Index(...)` 一并对齐
2. **EP-4 stats tz-aware 兼容**：`/content-reviews/stats` 接口聚合 `pending_since` 时统一使用 `datetime.now(UTC)`，避免 naive vs aware 比较的 `TypeError`
3. **phase 矩阵测试同步**：`tests/unit/models/test_phase_step_hook.py` + `test_curation_phase_step_hook.py` + `services/test_business_workflow_service.py` 三文件同步至四阶段矩阵

---

### 📝 文档同步

- `docs/architecture.md`（§ 内容审核门 + § 业务阶段双列 + § API 路由汇总 + § 错误码）
- `docs/features.md`（Feature-022 完整章节）
- `docs/business-workflow.md`（§ 1 三阶段升级为四阶段 / § 2 阶段判据 / § 4 状态机 / § 5 队列 / § 7 错误码）
- 时间戳：所有三份均为 `2026-05-29`

---

### 🚪 章程合规

- ✅ Article I (TDD)：78 用例先红后绿
- ✅ Article IV (API 一致性)：所有新接口走 `SuccessEnvelope` / `AppException` 标准信封
- ✅ Article IX (无台账)：未保留任何下线哨兵或迁移台账
- ✅ pre-push drift & spec-compliance scan：全绿

---

### 🔍 Review 重点

1. **迁移 0021 正/反向**：建议在 staging 上 `alembic upgrade head` 然后 `alembic downgrade -1` 来回一次确认无残留
2. **审核门两层闸的 fail-closed 默认值**：`review_state IS NULL` 视同 `pending_review`，确保任何未走完精选的 cvclf 都会被路由层拒绝
3. **30s 热开关的可观测性**：`PATCH /admin/review-gate` 后是否在 30s 内全部 worker 实例都看到新值（依赖 `task_channel_configs` 通道配置缓存机制）

---

### 🚦 合并后下一步

- 处理 pre-existing 残留（27 fail + 11 err，**与 F-022 无关**），建议在新分支 `tech-debt/pre-existing-cleanup` 中独立处理
- 选择下一个 Feature 推进
