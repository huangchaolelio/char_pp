# Feature-018 新增错误码登记

**对齐**: 章程 v1.4.0 原则 IX「错误码集中化单一事实来源」。本表所列错误码 MUST 同步登记到:
1. `src/api/errors.py::ErrorCode` 枚举
2. `src/api/errors.py::ERROR_STATUS_MAP`
3. `src/api/errors.py::ERROR_DEFAULT_MESSAGE`

## 新增错误码（3 个）

| Code | HTTP | 触发场景 | details 示例 |
|------|------|---------|-------------|
| `INVALID_PHASE_STEP_COMBO` | 400 | `?business_phase=` / `?business_step=` / `?task_type=` 三参数语义矛盾（研究 R6） | `{"conflict": "phase_step_task_type_mismatch", "phase": "TRAINING", "step": "extract_kb", "task_type": "athlete_diagnosis"}` |
| `PHASE_STEP_UNMAPPED` | 500 | ORM `before_insert` 钩子无法为写入行派生 `(phase, step)`（Clarification Q4 的 fail-fast 信号） | `{"table": "analysis_tasks", "task_type": "unknown_value_x"}` |
| `OPTIMIZATION_LEVERS_YAML_INVALID` | 500 | `config/optimization_levers.yml` 加载时 schema 校验失败（启动时 fail-fast） | `{"reason": "type must be one of runtime_params/algorithm_models/rules_prompts", "offending_key": "POSE_BACKEND"}` |

## 映射表补丁

```python
# src/api/errors.py::ErrorCode 新增三值
INVALID_PHASE_STEP_COMBO = "INVALID_PHASE_STEP_COMBO"
PHASE_STEP_UNMAPPED = "PHASE_STEP_UNMAPPED"
OPTIMIZATION_LEVERS_YAML_INVALID = "OPTIMIZATION_LEVERS_YAML_INVALID"

# ERROR_STATUS_MAP 补
ErrorCode.INVALID_PHASE_STEP_COMBO: HTTPStatus.BAD_REQUEST
ErrorCode.PHASE_STEP_UNMAPPED: HTTPStatus.INTERNAL_SERVER_ERROR
ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID: HTTPStatus.INTERNAL_SERVER_ERROR

# ERROR_DEFAULT_MESSAGE 补
ErrorCode.INVALID_PHASE_STEP_COMBO: "业务阶段/步骤与任务类型不匹配"
ErrorCode.PHASE_STEP_UNMAPPED: "业务阶段/步骤派生失败（内部错误）"
ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID: "优化杠杆台账配置加载失败"
```

## 不改名 / 不换状态码（已发布错误码保护）

本 Feature **不修改**任一 Feature-017 已发布的错误码含义或 HTTP 状态。

## 合约测试覆盖

- `tests/contract/test_tasks_phase_step_filter_contract.py`:
  - `?business_phase=INFERENCE&task_type=kb_extraction` → 400 `INVALID_PHASE_STEP_COMBO`
- `tests/unit/models/test_phase_step_hook.py`:
  - 直接 ORM 写入未知 `task_type` → 钩子抛 `ValueError("PHASE_STEP_UNMAPPED...")`
- `tests/unit/services/test_optimization_levers_service.py`:
  - 加载非法 YAML（`type: foo`）→ 启动失败，抛 `OPTIMIZATION_LEVERS_YAML_INVALID`
