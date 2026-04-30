# Specification Quality Checklist: KB Per-Category Lifecycle

**Purpose**: 在进入规划阶段之前验证规范的完整性和质量
**Created**: 2026-04-30
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

> 说明：为契合项目章程（`API 设计规范` / `业务阶段映射` 条款），本规范在「功能需求」与「业务阶段映射」段落中出现了 **必要的接口路径 / 错误码 / 表名 / 列名**。这是 project constitution 强制产物（章程原则 X + API v1.4.0 统一信封要求），非自由增添的实现细节。

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- 与章程对齐点：
  - `docs/business-workflow.md` § 4.2 "单 active 约束" 需要在 plan 阶段同步由"全局单 active"改写为"per-category 单 active"。
  - 新增业务步骤 `tip_batch_activate` 需扩 `docs/business-workflow.md` § 2 / § 4 / § 7.2。
  - 新增错误码（`KB_EMPTY_COVERAGE` / `NO_ACTIVE_KB_FOR_CATEGORY` / `STANDARD_ALREADY_UP_TO_DATE` / `TIP_BATCH_NOT_FOUND` / `TIP_BATCH_NOT_DRAFT`）必须在 plan 阶段登记到 `src/api/errors.py::ErrorCode`，并更新 `docs/business-workflow.md` § 8 错误码总表。
- 与 Feature-018 `workflow-standardization` 无冲突：018 负责把三阶段业务模型成文化，019 是在其 STANDARDIZATION 阶段内部"让每个类别独立走状态机"。
- 回滚剧本 R-019 已在「业务阶段映射 - 回滚剧本」中承诺要在 plan 阶段落地。
- Items marked incomplete require spec updates before `/speckit.clarify` or `/speckit.plan`
