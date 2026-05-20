# Feature-021 错误码登记

**集中登记位置**：`src/api/errors.py`（`ErrorCode` 枚举 + `ERROR_STATUS_MAP` + `ERROR_DEFAULT_MESSAGE` 三张表）
**业务文档同步位置**：`docs/business-workflow.md § 7.4`（已扩展）
**章程依据**：原则 IX（错误码集中化）+ 原则 X（错误码前缀变化必同步业务文档）

---

## 错误码清单

| 错误码 | HTTP | 默认消息 | 触发场景 | 可重试 |
|-------|------|---------|---------|-------|
| `CURATION_REQUIRED` | 409 | "Video has not been curated; submit POST /tasks/curation first." | KB 抽取提交时该视频无 `video_curation_jobs.status=success` 记录 | 否（先跑清洗再重提）|
| `LOW_QUALITY_SKIP` | 409(业务) | "Video curation marked accepted_duration_ratio == 0; KB extraction skipped." | 清洗后无任何 accepted 分段，DAG 内 `download_video` 短路 | 否（人工覆盖后手动 rerun）|
| `RUBRIC_INVALID` | 422 | "Curation rubric file failed schema validation." | 规范 YAML 缺字段 / 类型错误 / 阈值越界 / version 不匹配 | 否（修文件 + 重新部署）|
| `RUBRIC_VERSION_NOT_FOUND` | 404 | "Requested curation_rubric_version not found in src/config/curation_rubric/." | 提交时声明的版本号无对应文件 | 否 |
| `CURATION_TIMEOUT` | 500 | "Curation job exceeded timeout and was reaped." | 作业 / 单分段 LLM 超出 `CURATION_JOB_TIMEOUT_SECONDS` / `CURATION_LLM_TIMEOUT_SECONDS` | 视情况（重提）|
| `CURATION_LLM_UNAVAILABLE` | 409(业务) | "LLM unavailable for ambiguous segment; segment marked uncertain." | 模糊区间分段需 LLM 但 Venus + OpenAI 均不可用 | 否（不阻断作业，仅落 segment.rejection_reason）|
| `CURATION_RUBRIC_MISMATCH` | 409 | "Submitted curation_rubric_version mismatches existing successful job; pass force=true to start a new job." | 同一视频已有 success 作业，再次提交时声明的 rubric_version 与既有不一致且未带 force | 否（用 force=true 强制新建）|

---

## `ErrorCode` 枚举（Python 形态）

```python
# src/api/errors.py（节选）
class ErrorCode(str, Enum):
    # ... 既有错误码 ...

    # === Feature-021 内容清洗 ===
    CURATION_REQUIRED            = "CURATION_REQUIRED"
    LOW_QUALITY_SKIP             = "LOW_QUALITY_SKIP"
    RUBRIC_INVALID               = "RUBRIC_INVALID"
    RUBRIC_VERSION_NOT_FOUND     = "RUBRIC_VERSION_NOT_FOUND"
    CURATION_TIMEOUT             = "CURATION_TIMEOUT"
    CURATION_LLM_UNAVAILABLE     = "CURATION_LLM_UNAVAILABLE"
    CURATION_RUBRIC_MISMATCH     = "CURATION_RUBRIC_MISMATCH"
```

```python
ERROR_STATUS_MAP[ErrorCode.CURATION_REQUIRED]            = 409
ERROR_STATUS_MAP[ErrorCode.LOW_QUALITY_SKIP]             = 409   # 业务结果，不直接返回客户端；仅写入 extraction_jobs.error_code
ERROR_STATUS_MAP[ErrorCode.RUBRIC_INVALID]               = 422
ERROR_STATUS_MAP[ErrorCode.RUBRIC_VERSION_NOT_FOUND]     = 404
ERROR_STATUS_MAP[ErrorCode.CURATION_TIMEOUT]             = 500
ERROR_STATUS_MAP[ErrorCode.CURATION_LLM_UNAVAILABLE]     = 409   # 业务结果，不直接返回客户端；仅写入 segment.rejection_reason
ERROR_STATUS_MAP[ErrorCode.CURATION_RUBRIC_MISMATCH]     = 409
```

> **章程 IX 一致性说明**：``test_error_codes_contract.py`` 的 CI 守卫要求所有
> ``ErrorCode`` 的 HTTP 状态在
> ``{400, 401, 404, 409, 410, 422, 500, 502, 503}`` 集合内（不允许 200）。
> 因此 ``LOW_QUALITY_SKIP`` 与 ``CURATION_LLM_UNAVAILABLE`` 虽是"业务结果型"
> （不通过 ``AppException`` 返回客户端），仍登记为 409 —— 与
> ``KB_CONFLICT_UNRESOLVED`` 同语义档位。它们的实际作用在下一节"业务结果型
> 错误码处理约定"中说明。

---

## 业务结果型错误码处理约定

`LOW_QUALITY_SKIP` 与 `CURATION_LLM_UNAVAILABLE` 不通过 ``AppException`` 路径
返回给 HTTP 客户端，而是作为内部信号写入 DB 字段：

- **`LOW_QUALITY_SKIP`**：DAG 第 1 步 ``download_video`` 在检测到清洗作业
  ``accepted_duration_ratio == 0`` 时抛 ``RuntimeError("LOW_QUALITY_SKIP: ...")``。
  Orchestrator 将其转写为 ``extraction_jobs.status='failed'`` +
  ``error_code='LOW_QUALITY_SKIP'`` + ``error_message`` 含 ``curation_job_id``
  反查锚点。任务监控接口以 ``extraction_jobs.error_code = 'LOW_QUALITY_SKIP'``
  字段筛选可定位"被业务跳过的作业"——与"真实失败"在 UI 上以错误码前缀区分（运维
  grep 友好）。**不调 LLM、不耗 token**（FR-009）。
- **`CURATION_LLM_UNAVAILABLE`**：清洗任务在模糊区间 ``(0.3, 0.7)`` 调 LLM 失败
  时，写入 ``video_curation_segment_results.rejection_reason = 'curation_llm_unavailable'``
  + ``auto_decision = 'uncertain'``，**不抛 AppException**，单段失败不传染整个
  作业（``video_curation_jobs.status`` 仍可为 ``success``）。

---

## 验收（合约测试）

每个错误码至少 1 个合约测试用例，文件位置参见 `plan.md::tests/contract/`：

- `test_kb_extraction_curation_gate.py`：`CURATION_REQUIRED` + `LOW_QUALITY_SKIP` 双场景
- `test_submit_curation.py`：`RUBRIC_INVALID` + `RUBRIC_VERSION_NOT_FOUND` + `CURATION_RUBRIC_MISMATCH`
- `test_get_curation_job.py`：`CURATION_TIMEOUT`（mock 超时分支）
- `test_override_curation_segment.py`：覆盖参数越界 → 既有 `VALIDATION_FAILED`（非本 feature 新增）
