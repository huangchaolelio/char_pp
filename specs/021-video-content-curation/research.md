# 阶段 0 · 研究产出 — Feature-021

**分支**: `021-video-content-curation`
**日期**: 2026-05-18
**输入**: [spec.md](./spec.md) + [plan.md](./plan.md)

> 本文件回答"为什么这样实现"。所有决策都由 spec 的 5 题 Clarifications 与既有项目惯例锚定；无新增 NEEDS CLARIFICATION。

---

## R1 · 与现有 `kb_extraction_pipeline` 编排骨架的对齐

**决策**：清洗作业**不**用 6 步 DAG（download → pose ∥ audio_transcribe → visual_kb ∥ audio_kb → merge）这种重型骨架，而是**单步任务 + 内部 N 段并行决策**。

**理由**：

- KB 抽取的 DAG 之所以分 6 步，是因为不同 wave 之间存在产物依赖（视觉 / 音频两路在 wave3 才能跑、wave4 合并）；清洗的判据每段独立、无段间依赖，强行套 DAG 反而增加 `pipeline_steps` 行数与 5 倍状态机字段。
- 清洗本身不产出磁盘 artifact（pose.json / transcript.json 已由 F-016 + F-014 落地），决策结果直接入 DB 即可，无需 `output_artifact_path` 语义。
- 复用 `services/kb_extraction_pipeline/orchestrator.py` 的 timeout / retry / 幂等 patterns（直接抄 retry_policy.py），但用单步骨架而非 DAG。

**拒绝替代方案**：

1. *把清洗当 KB DAG 的"第 -1 步"内联进 6 步 DAG* — 否决。会让 KB 失败重跑必带清洗重跑、覆盖率监控混乱、`pipeline_steps` 表语义被污染。spec FR-001 明确要求"独立任务类型"。
2. *按教练人 / 按 tech_category 批量并行* — 否决。本期不必要的并行复杂度；spec 性能目标在单条 30 秒 / 批量 10 条 90 秒下足够富余。

---

## R2 · 清洗规范文件存放位置（Q1 决议落地）

**决策**：`src/config/curation_rubric/v1.yaml` + `src/config/curation_rubric/schema.json`，与代码同源；DB 仅存版本字符串。

**理由**：

- 与既有 `src/config/video_classification.yaml`（F-004 教练规则）、`src/config/keywords/tech_hint_keywords.json`（F-002 关键词字典）路径同构；运营改规则的 PR 流程可直接复用。
- 顶层 `config/` 目录是"静态业务字典"（如 `coach_directory_map.json`、`tech_classification_rules.json`），变更频率更低、无 schema 强校验；清洗规范要求启动期 + 任务排队前都做 schema 校验，归到 `src/` 内更合适。
- `schema.json` 与 `v1.yaml` 同目录便于 `tests/unit/test_rubric_loader.py` 自动遍历"目录下所有 vN.yaml 必须通过 schema 校验"，CI 防御新规范上线时漏校验。

**版本号约定**：`vN`（`v1` / `v2` / ...），仅整数递增。文件内顶层 `version: vN` 字段必须与文件名匹配，不匹配 ⇒ `RUBRIC_INVALID:version_filename_mismatch`。

**初版 v1.yaml 字段骨架**（Phase 1 在 data-model.md 详化）：

```yaml
version: v1
description: "Feature-021 内容清洗规范初版"
thresholds:
  validity_score_accept: 0.7      # ≥ 此阈值直接 accepted
  validity_score_reject: 0.3      # ≤ 此阈值直接 rejected
  low_quality_ratio: 0.3          # accepted_duration_ratio < 此阈值标 low_quality=true
  short_video_seconds: 30         # 视频总时长 < 此值 short_video=true
  min_segment_seconds: 5          # 单分段 < 此时长直接 rejected:too_short
rules:
  tech_keyword:                   # 第 1 维：教学关键词命中
    enabled: true
    weight: 0.35
    keywords_ref: "src/config/keywords/tech_hint_keywords.json"  # 复用既有字典
  non_teaching:                   # 第 2 维：非教学排除
    enabled: true
    weight: 0.25
    keywords:
      match:                      # 命中即重罚
        - "比赛"
        - "决赛"
        - "采访"
        - "祝大家"
  coach_dominance:                # 第 3 维：目标教练主导
    enabled: true
    weight: 0.20
    min_dominance_ratio: 0.6      # 目标教练讲解时长占比 ≥ 0.6 才不扣分
  topic_relevance:                # 第 4 维：与 tech_category 主题相关
    enabled: true
    weight: 0.15
    keywords_ref: "src/config/tech_classification_rules.json"   # 反向用 21 类关键词
  duration_floor:                 # 第 5 维：最短时长硬约束
    enabled: true
    weight: 0.05
llm_fallback:
  enabled: true
  invoke_when_score_in: [0.3, 0.7]
  prompt_template: "src/config/curation_rubric/prompts/segment_decision_v1.md"
  timeout_seconds: 5
  unavailable_decision: "uncertain"
```

---

## R3 · 算法骨架（Q4 决议落地）

**决策**：规则路 5 维线性加权 → `validity_score = Σ(dim_score × weight)`；得分 ≥ 0.7 直 accepted、≤ 0.3 直 rejected；`(0.3, 0.7)` 调一次 LLM。

**理由**：

- 5 维（教学关键词 / 非教学排除 / 教练主导 / 主题相关性 / 最短时长硬约束）覆盖 spec FR-006 列出的全部判据维度，缺一不可。
- 线性加权简单可解释（每条结果可附 `dim_breakdown` 用于审计），与 KB 抽取里 `transcript_tech_parser` 的"关键词加权"骨架同构，工程师上手 0 学习成本。
- LLM 仅在模糊区间调用 ⇒ 单条视频 N=20 段中只有 ≤ 4 段会调 LLM ⇒ 单条总耗时主要由本地规则决定，符合性能目标 30 秒 p95。
- LLM 不可用时落 `uncertain`，**不阻断作业** — 这是与教练侧 KB 抽取 `LLM_UNCONFIGURED:` fail-fast 的关键差异：清洗的目的是"提供高信噪比输入"，遇到不确定的段标 uncertain（最终下游不消费）比直接失败更稳健。

**LLM Prompt 契约**（细节归 `src/config/curation_rubric/prompts/segment_decision_v1.md` 维护）：

输入：分段对应 transcript 文本 + `tech_category` + 当前规则路 `validity_score` + 各维度 breakdown
输出（JSON 强约束）：`{"decision": "accepted|rejected|uncertain", "validity_score": 0.0-1.0, "rejection_reason": "...|null", "rationale": "..."}`

LLM 返回非 JSON / 字段缺失 ⇒ 该段落 `uncertain` + `rejection_reason="llm_response_invalid"`，单段失败不传染整个作业。

**拒绝替代方案**：

1. *纯规则路（spec Q4 选项 B）* — 否决。spec SC-001 召回率 ≥ 0.85 在"教练风格各异"的数据上，纯关键词命中难以达到；LLM 兜底是必要的边界保险。
2. *全量 LLM（Q4 选项 C/D）* — 否决。每段都调 LLM 会让清洗成本超过它给 KB 抽取省的成本，违反 SC-002 LLM token 下降目标。

---

## R4 · 队列归属与超时设计（Q2 决议落地）

**决策**：复用 `default` 队列（concurrency=1）；作业级超时默认 600 秒，单分段 LLM 超时 5 秒。

**理由**：

- spec Q2 选项 B 已锁定 `default` 队列；与 `scan_cos_videos` / `housekeeping` 同列。
- `default` worker 现有 concurrency=1，串行执行不会让 ffmpeg / KB 抽取竞争 CPU；一条清洗任务峰值就是 N 段 × LLM 调用，N 通常 ≤ 30。
- 作业级超时 600 秒 = 30 段 × (本地规则 ≈ 0.5s + LLM ≈ 5s) × 1.5 倍余量 — 与既有 `EXTRACTION_STEP_TIMEOUT_SECONDS` 量级一致，可复用 `housekeeping_task::sweep_orphan_jobs` 的孤儿回收兜底。
- 超时配置走 `.env`：新增 `CURATION_JOB_TIMEOUT_SECONDS=600` / `CURATION_LLM_TIMEOUT_SECONDS=5`，与 KB 提取的 `EXTRACTION_*_TIMEOUT_SECONDS` 命名风格一致。

---

## R5 · 视频级摘要派生时机（Q3 决议落地）

**决策**：作业 success 时一次性派生并落库；任何分段被人工覆盖时**事务内重算**并 UPDATE 同一行。

**理由**：

- spec FR-012 要求"视频级摘要必须在任何分段覆盖发生后自动重算"。
- 落 1 张表（`video_curation_jobs`）+ 1 张子表（`video_curation_segment_results`）最自然 — 作业表存摘要快照，覆盖时 PATCH 触发"重新读子表 → 重新派生摘要 → UPDATE 作业表"，事务原子。
- 不引入"派生视图"，避免运营查询时跨表 join 开销 + 视图与覆盖时序错位。

**双阈值消费门**（FR-009）：

| 摘要状态 | KB 抽取行为 | 错误码 |
|---------|-----------|-------|
| `accepted_duration_ratio == 0` | 业务短路 | `LOW_QUALITY_SKIP:` |
| `accepted_duration_ratio ∈ (0, 0.3)` | 正常执行 + warning | warning 写在 `extraction_jobs.output_summary.curation_warning="low_quality"` |
| `accepted_duration_ratio ≥ 0.3` | 正常执行 | — |
| 视频无 `video_curation_jobs.status=success` 记录 | 立即拒绝 | `CURATION_REQUIRED:` |

警告标记**不**走错误码（KB 抽取作业仍 success），走 `extraction_jobs.output_summary` JSONB 字段；任务监控接口在前端可读。

---

## R6 · 人工覆盖与 KB 重抽解耦（Q5 决议落地）

**决策**：覆盖只更新 `effective_decision` + 视频级摘要；不级联触发 KB 重抽；`coach_video_classifications` 增 `kb_stale_after_override` bool 字段供监控查询。

**理由**：

- spec Q5 选项 A 明确"不自动触发"，用既有 `POST /extraction-jobs/{id}/rerun` 手工重跑路径。
- `kb_stale_after_override` 字段语义是：该视频的 `last_curation_job_id` 上至少有一行 `override_decision IS NOT NULL` 且时间晚于 `extraction_jobs.completed_at`。
- 派生方式：触发器或 service 层手动维护两选一 — 选**手动维护**（service 层），因为：
  - 触发器逻辑跨 3 张表（`coach_video_classifications` / `video_curation_segment_results` / `extraction_jobs`），DB 触发器维护成本高
  - service 层维护与覆盖接口同事务，符合现有项目"业务逻辑只在 services/"的章程附加约束

**字段语义清单**（`video_curation_segment_results` 同行扩展）：

```
auto_decision         VARCHAR(16) NOT NULL  -- accepted | rejected | uncertain
override_decision     VARCHAR(16) NULL      -- accepted | rejected
override_user         VARCHAR(64) NULL      -- 操作员标识（沿用现有内部账号体系）
override_reason       TEXT        NULL
overridden_at         TIMESTAMP   NULL
effective_decision    VARCHAR(16)            -- 计算列：COALESCE(override_decision, auto_decision)
```

`effective_decision` 用 PostgreSQL `GENERATED ALWAYS AS ... STORED` 计算列实现，避免应用层每次 query 重算与漏算。

---

## R7 · 与 Feature-014 KB 抽取的契约对接

**决策**：扩展 `POST /tasks/kb-extraction` 路径在排队前增加 2 个前置门，**不**改 `kb_extraction_pipeline` 内部 6 步 DAG。

**理由**：

- 前置门在路由 / submission 层就足够拦截，DAG 内部继续按"读 `extraction_jobs.tech_category` + 关联视频" 拉数据；只把"读分段集合"的入口换成"`SELECT segment_index FROM video_curation_segment_results WHERE job_id = (SELECT last_curation_job_id ...) AND effective_decision='accepted'`"。
- 修改面：
  - `services/submission_service.py`（或 `tasks_service.py`）排队前 2 处校验
  - `services/kb_extraction_pipeline/step_executors/audio_kb_extract.py` + `visual_kb_extract.py` 读分段处加 1 行 join 过滤
  - 不动 orchestrator / merger / artifact_io
- 关键护栏：`tests/integration/test_kb_extract_consumes_accepted_only.py` 在 KB 作业完成后断言"被 rejected 的分段 cos_object_key 从未在 LLM Prompt 拼装中出现"

**bypass 开关**（应急回滚）：`task_channel_configs.kb_extraction.config_payload.bypass_curation_gate=true` ⇒ 跳过 2 个前置门、读全量分段；30s TTL 热配置 + 必走审计日志。

---

## R8 · 错误码新增清单与登记位置

集中登记到 `src/api/errors.py`，同步 4 处。本 feature 共 7 个错误码：

| 错误码 | HTTP 状态 | 触发场景 | 可重试 |
|-------|----------|---------|-------|
| `CURATION_REQUIRED` | 409 | 视频无 `video_curation_jobs.status=success` 记录就提交 KB 抽取 | 否（先跑清洗） |
| `LOW_QUALITY_SKIP` | 200 + 业务结果 | 视频清洗后 `accepted_duration_ratio == 0` | 否（人工覆盖后可手动 rerun） |
| `RUBRIC_INVALID` | 422 | 规范文件 schema 校验失败 | 否（修文件后重提） |
| `RUBRIC_VERSION_NOT_FOUND` | 404 | 提交时声明的 `curation_rubric_version` 找不到对应文件 | 否 |
| `CURATION_TIMEOUT` | 500 | 清洗作业超时被孤儿回收 | 视情况 |
| `CURATION_LLM_UNAVAILABLE` | 200 + warning | 模糊区间需 LLM 但不可用，分段落 uncertain | 否 |
| `CURATION_RUBRIC_MISMATCH` | 409 | 重跑覆盖 / 重算时声明的 `rubric_version` 与原作业不一致 | 否（force=true 强制新建） |

**注**：`LOW_QUALITY_SKIP` / `CURATION_LLM_UNAVAILABLE` 是"业务跳过"型 — 不抛 `AppException`，而是写到 `extraction_jobs.error_code` 或 `video_curation_segment_results.rejection_reason`，但纳入错误码集中表防止重名。

---

## R9 · 性能与基准测试设计

**预算**：

| 场景 | p50 | p95 | 上限 |
|-----|-----|-----|-----|
| 单条视频清洗（N=20 段，≤ 4 段调 LLM） | 15 s | 30 s | 60 s（超时熔断） |
| 批量 10 条视频（concurrency=1 串行） | 60 s | 90 s | 360 s（spec SC-006 上限） |
| 规范文件加载（YAML + jsonschema） | 10 ms | 30 ms | 50 ms |
| 单分段决策（仅规则路） | 50 ms | 200 ms | 1 s |
| 单分段决策（命中 LLM 路） | 3 s | 5 s | 10 s（LLM 调用超时） |

**基准回归脚本**：复用现有 `scripts/run_reference_regression.py` 思路，新增 `scripts/run_curation_benchmark.py`：

1. 拉取人工标注样本集（≥ 30 条）
2. 跑清洗 → 与人工标注对照计算 precision / recall
3. 跑相同样本对照 KB 抽取 token 量（清洗前 vs 清洗后）
4. 跑相同 `tech_category` 多视频对照"技术术语重叠率"

输出：`specs/021-video-content-curation/benchmark/v1_baseline.json`（建立基线，未来回归对照）

---

## R10 · 不需要解决的问题

明确**不在本 feature 范围内**的事项（spec Assumptions 已声明，此处复述供 plan 阶段对齐）：

1. 清洗规范的 Web 编辑 UI / 在线发布接口 — 永不做（spec Q1 结论 + Assumptions 显式拒绝）
2. 清洗结果 / 覆盖记录的"遗忘权"删除接口 — 不做（与 Feature-020 同风格，运维直接 DB 处置）
3. Feature-021 提案 doc（`docs/feature-021-proposal.md`）里的 V2 层级分类升级 — 与本 feature 解耦，留独立 Feature 处理；本期清洗规范使用扁平 21 类，未来升级时 rubric 文件按 V2 路径键入即可
4. 运动员侧（Feature-020）AthleteVideoClassification 的清洗 — 显式不做；运动员视频走诊断链路，不进 KB 抽取，无清洗诉求
5. 自动学习清洗阈值（基于反馈数据反向调整 rubric） — 留给未来 Feature；本期靠"人工调 PR + 跑 benchmark"做迭代

---

## 决策汇总

| 编号 | 决策 | 决策来源 |
|-----|------|---------|
| R1 | 单步任务（不套 6 步 DAG） | 编排惯例 + 性能预算 |
| R2 | 规范放 `src/config/curation_rubric/`（YAML） | spec Clarifications Q1 |
| R3 | 规则路 5 维加权 + LLM 兜底（仅 0.3-0.7 区间） | spec Clarifications Q4 |
| R4 | 复用 `default` 队列，concurrency=1，超时 600 秒 | spec Clarifications Q2 |
| R5 | 视频级摘要在 success / 覆盖时事务内派生 | spec FR-012 |
| R6 | 覆盖不级联触发 KB 重抽，加 `kb_stale_after_override` 提示位 | spec Clarifications Q5 |
| R7 | KB 抽取前置门在 submission 层 + 内部读分段时 join 过滤；`bypass_curation_gate` 应急开关 | 章程 § 9 杠杆约束 |
| R8 | 7 个错误码登记 | 章程原则 IX |
| R9 | benchmark 脚本 + v1 baseline | 章程原则 VIII |
| R10 | 显式排除 5 类范围外事项 | spec Assumptions |

阶段 0 完成。所有决策无 NEEDS CLARIFICATION，门控 ✅，进入阶段 1（data-model / contracts / quickstart）。
