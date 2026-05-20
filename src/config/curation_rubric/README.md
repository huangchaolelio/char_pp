# 内容清洗规范文件 — 作者指引

本目录是 Feature-021（视频内容清洗）的清洗规范单一事实来源（章程原则 X
"业务流程对齐" + 章程原则 IX "错误码集中化" 适用）。

> **TL;DR**：要改清洗规则？复制 `vN.yaml` 为 `v(N+1).yaml`，改字段，
> 跑 `pytest tests/unit/test_rubric_loader.py`，提 PR。**不要**直接改
> 已发布的旧版本。

---

## 目录结构

```
src/config/curation_rubric/
├── README.md                 # 本文件
├── schema.json               # jsonschema (draft-07) — 不要手改
├── v1.yaml                   # 已发布版本 1（永不修改）
├── v2.yaml                   # 已发布版本 2（永不修改）
└── prompts/
    └── segment_decision_v1.md  # LLM 兜底 Prompt 模板（与 yaml.llm_fallback.prompt_template 对齐）
```

---

## 版本号约定

- 文件名固定 `vN.yaml`（N 为正整数，**不允许** 1.5 / v1a / v1.0）
- 文件顶层 `version` 字段必须与文件名匹配，否则 `RUBRIC_INVALID:version_filename_mismatch`
- 已发布版本（已合并到 master）**永不允许**修改字段值；只能新发版本
- 历史作业行 `video_curation_jobs.curation_rubric_version` 留痕，按版本号
  回查 git 历史即可还原当时判据

---

## 改规则的标准工作流

1. `cp v$(LATEST).yaml v$((LATEST+1)).yaml`
2. 把新文件顶层 `version` 改成 `vN+1`
3. 修改字段（关键词列表 / 权重 / 阈值 / LLM 开关 等）
4. 本地校验：

   ```bash
   /opt/conda/envs/coaching/bin/python3.11 -m pytest \
       tests/unit/test_rubric_loader.py -v
   ```

   该测试遍历所有 `vN.yaml` 跑 schema 校验；任何破损都会失败。

5. 可选：`/opt/conda/envs/coaching/bin/python3.11 -m pytest \
       tests/unit/test_decision_engine_rule_only.py \
       tests/unit/test_decision_engine_llm_fallback.py -v`
   验证决策引擎在新阈值下对样本文本仍输出预期分类。

6. 提 PR；CI 守卫 (`make spec-compliance`, `make drift-changed`,
   pytest CI) 自动校验。

7. 合并发版后，新清洗任务可显式声明 `curation_rubric_version: vN+1`：

   ```bash
   curl -X POST http://localhost:8080/api/v1/tasks/curation \
        -H 'Content-Type: application/json' \
        -d '{"coach_video_classification_id": "...",
             "curation_rubric_version": "v2"}'
   ```

   不传 `curation_rubric_version` 时自动取最新版本。

8. 同视频既有 success 作业 `curation_rubric_version` 与本次提交不一致 +
   `force=false` ⇒ `409 CURATION_RUBRIC_MISMATCH`，提示用 `force=true`
   显式新建。

---

## 字段语义速查

完整 schema 在 `schema.json`；以下是关键字段速查：

### `thresholds` — 决策阈值

| 字段 | 取值 | 默认 (v1) | 含义 |
|------|------|----------|------|
| `validity_score_accept` | 0–1 | 0.7 | 规则路总分 ≥ 此值直接 accepted |
| `validity_score_reject` | 0–1 | 0.3 | 规则路总分 ≤ 此值直接 rejected |
| `low_quality_ratio` | 0–1 | 0.3 | 视频级 `accepted_duration_ratio < 此值` ⇒ `low_quality=true` |
| `short_video_seconds` | int | 30 | 视频总时长 < 此值 ⇒ `short_video=true` |
| `min_segment_seconds` | int | 5 | 单分段 < 此时长直接打 0 分（`duration_floor` 维度）|

> **注意阈值连带影响**：调 `low_quality_ratio` 会影响 KB 抽取 `download_video`
> 的 `curation_warning='low_quality'` 触发条件（spec FR-009 双阈值消费门）。
> 调 `validity_score_accept/reject` 会决定 LLM 兜底带宽 `(reject, accept)`。

### `rules` — 5 维加权决策

每个规则有 `enabled` (bool) + `weight` (0–1)；权重之和无强制 = 1，
但建议保持 1.0 以让 `validity_score` 范围直观。

| 规则 | weight (v1) | 关键参数 |
|------|------------|----------|
| `tech_keyword` | 0.35 | `keywords_ref` 指向教学关键词字典 JSON |
| `non_teaching` | 0.25 | `keywords.match` 内联非教学排除词（命中即重罚）|
| `coach_dominance` | 0.20 | `min_dominance_ratio` 启发式判定主导率最低门 |
| `topic_relevance` | 0.15 | `keywords_ref` 指向 21 类技术分类规则 JSON |
| `duration_floor` | 0.05 | （硬约束：< `min_segment_seconds` 直接 0 分）|

**禁用某条规则的影响**：把 `enabled: false` 等价于 `weight: 0`；总分仍能算，
但少一个维度信号。**只调 weight 不要禁用**——除非你明确知道禁用后规则路得分
分布的影响（规则路得分上限 = sum(enabled weights)）。

### `llm_fallback` — LLM 兜底配置

| 字段 | 含义 |
|------|------|
| `enabled` | 关闭 LLM 后所有模糊区间分段一律落 `unavailable_decision` |
| `invoke_when_score_in` | 得分进入此 `[lo, hi]` 闭区间才调 LLM；建议与 `validity_score_reject/accept` 一致 |
| `prompt_template` | 相对仓库根的 markdown 模板路径；占位符见 `decision_engine.py::_llm_fallback_decide` |
| `timeout_seconds` | 单次 LLM 调用超时（与 `CURATION_LLM_TIMEOUT_SECONDS` 重叠时取后者）|
| `unavailable_decision` | LLM 不可用时分段落入哪档；`uncertain` 是默认（不阻断作业）|

---

## 不要做的事

- ❌ **不要直接改已发布的 `vN.yaml`** — 历史作业行的 `curation_rubric_version`
  已留痕；改老版本会让回查产生不一致。
- ❌ **不要新增字段而不改 schema.json** — schema 用 `additionalProperties: false`，
  新字段会被拒绝加载。
- ❌ **不要把 weight 总和调到 0**——所有维度同时禁用会让规则路恒为 0，全部
  分段直接 rejected。
- ❌ **不要在 v 文件里写注释引用其它 v 文件**——单文件自含，便于按版本号回查。

---

## 应急回滚

如果某版规范误伤导致 KB 抽取大量 `LOW_QUALITY_SKIP`（参见
`docs/business-workflow.md § 10` 回滚剧本）：

1. 在 `.env` 设 `KB_EXTRACTION_BYPASS_CURATION_GATE=true`，重启 API + Worker；
   立即停止清洗门拦截，KB 抽取退回到读全量分段（即 F-021 上线前的行为）。
2. `git revert <bad-rubric-commit>`，回到上一个已知良好的版本。
3. 对受影响视频跑 `POST /api/v1/tasks/curation { force: true }` 重跑清洗。
4. 关闭 bypass：`KB_EXTRACTION_BYPASS_CURATION_GATE=false`，重启。

每次 bypass 命中会在 `extraction_jobs.output_summary.curation_bypass=true`
留痕，事后审计可定位。

---

## 进一步阅读

- 规范设计与决策：`specs/021-video-content-curation/research.md § R2 + R3`
- 数据流与摘要派生：`specs/021-video-content-curation/data-model.md § 4`
- 算法骨架（5 维加权 + LLM 兜底）：`src/services/curation/decision_engine.py`
- LLM 兜底 Prompt 契约：`prompts/segment_decision_v1.md`
- 业务工作流文档：`docs/business-workflow.md § 3.4`
