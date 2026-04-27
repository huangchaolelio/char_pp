# 实施计划: API 接口统一规范化与遗留接口下线

**分支**: `017-api-standardization` | **日期**: 2026-04-27 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/017-api-standardization/spec.md` 的功能规范

## 摘要

将现有 10 + 4 个路由文件（`src/api/routers/` 下 14 个文件，其中 3 个不在宪法原则 IX 明文列表：`admin.py`、`task_channels.py`、`video_preprocessing.py`，本 Feature 一并纳入规范）**一次性原子切换**到统一响应信封：

- **成功响应**：`{"success": true, "data": <业务载荷>, "meta": {page, page_size, total} | null}`
- **错误响应**：`{"success": false, "error": {"code": str, "message": str, "details": object | null}}`
- **已下线接口**：返回 `404 + error.code=ENDPOINT_RETIRED`，在 `error.details.successor` 中给出替代路径；保留哨兵路由不物理删除

**技术路径**：
1. 新增 `src/api/schemas/envelope.py` 定义 `ResponseEnvelope[T]`、`ErrorBody`、`PaginationMeta` 三个泛型 Pydantic v2 模型
2. 新增 `src/api/errors.py` 集中定义 `ErrorCode` 字符串枚举 + `AppException` 异常基类 + `exception_to_response` 全局异常处理器
3. 新增 `src/api/routers/_retired.py` 哨兵路由注册表，按 `RetirementLedger` 枚举 6 条已下线接口
4. 改造全部 14 个路由文件的 `response_model` 与 `HTTPException` 调用点
5. 改造全量 `tests/contract/` 断言路径（由 `body["data"]` 改为 `body["data"]`、由 `body["error"]["code"]` 改为 `body["error"]["code"]`，顶层新增 `body["success"]` 断言）
6. 同步改造前端调用与集成脚本（本 repo 内）

**关键决策**（来自 spec.md 澄清章节）：Big Bang 一次性切换、不保留任何兼容、下线返回 404+`ENDPOINT_RETIRED`、信封采用顶层 `success` 布尔位互斥方案。

## 技术背景

**语言/版本**: Python 3.11（虚拟环境 `/opt/conda/envs/coaching/bin/python3.11`，遵循项目规则）
**主要依赖**: FastAPI（路由与 OpenAPI 生成）、Pydantic v2（`model_config = ConfigDict(...)`）、SQLAlchemy 2.x AsyncSession（不涉及 schema 改动）、pytest（契约/集成测试）
**存储**: PostgreSQL（本 Feature 不改表）
**测试**: pytest + `httpx.AsyncClient` 契约测试（`tests/contract/`）+ 集成测试（`tests/integration/`）
**目标平台**: Linux 服务器（FastAPI + Uvicorn + Celery 多队列架构，Feature-013/016 已确立）
**项目类型**: Web 服务（单一后端，无前端代码；前端在本 repo `frontend/` 之外，不在本 Feature 范围）
**性能目标**: 信封改造引入的响应构造成本需 ≤ 单次响应总耗时的 5%；列表接口 p95 延迟增幅 ≤ 5ms
**约束条件**:
- 不改数据库 schema，不改 Celery 队列布局，不改业务行为
- 新旧信封不并存——合入日起，保留接口 100% 使用新信封、下线接口 100% 返回 `ENDPOINT_RETIRED`
- 必须同一 PR 内同步修改路由、契约测试、集成测试、前端调用、OpenAPI 文档
**规模/范围**:
- 路由文件：14 个（全部纳入规范）
- 路由端点：约 55+ 个（Phase 0 研究完成精确盘点）
- 待下线接口：6 条（FR-009 清单）
- 待下线哨兵路由：保留原路径+方法，处理器仅抛 `ENDPOINT_RETIRED`
- 错误码枚举：初版预计 25–30 个，覆盖已知业务错误 + 上游错误 + 保留错误（`INTERNAL_ERROR`、`ENDPOINT_RETIRED`、`VALIDATION_FAILED` 等）

## 章程检查

*门控: 必须在阶段 0 研究前通过. 阶段 1 设计后重新检查. *

**章程合规验证**:
- ✅ 规范包含量化精准度指标（本 Feature 为"接口规范化"非算法功能，SC-001~SC-009 定义了可度量的格式合规指标，作为原则 VIII 的等价替代）
- ✅ 无前端实现任务混入范围（前端调用更新由前端同事在同一 PR 内配合，但前端代码改动不列入本 Feature 的 tasks.md）
- ✅ 本功能不涉及 AI 模型（原则 VI 不适用）
- ✅ 本功能不涉及用户数据采集变更（原则 VII 不适用）
- ✅ **原则 IX 已于章程 v1.4.0 完成同步修订**（2026-04-27）：响应体格式条款已升级为"顶层 `success` 布尔位 + `data`/`meta` 与 `error` 互斥信封"，错误响应映射条款已升级为"`AppException` + 集中化 `ErrorCode` 映射"，并新增"已下线接口台账"配套要求。本 Feature 的 4 项澄清决策全部与新章程一致。

**API 接口规范验证要点（原则 IX v1.4.0 逐条对照）**:
- ✅ 版本前缀统一使用 `/api/v1/`（FR-001、FR-011 强化）
- ✅ 路由按资源划分（本 Feature 不合并资源，反而统一 14 个文件的前缀声明方式）
- ✅ 分页参数统一 `page` + `page_size`（FR-012 强化，并删除所有 `limit/offset` 残留）
- ✅ 响应体统一信封：成功 `{success:true, data, meta|null}` / 错误 `{success:false, error:{code,message,details}}`，与章程 v1.4.0 新规完全一致
- ✅ 分层职责：本 Feature 不移动业务逻辑；若在改造中发现路由层含业务逻辑，在 tasks.md 中新增"搬迁到 services/"子任务
- ✅ 错误响应：`AppException` + 集中化 `ErrorCode` 映射 + 全局异常处理器，与章程 v1.4.0「错误响应映射」段落逐字对齐
- ✅ ENDPOINT_RETIRED 哨兵路由 + RetirementLedger 台账双份维护，与章程 v1.4.0「已下线接口台账」段落一致
- ✅ 新增/变更接口在 `contracts/` 下提供契约，并先于实现创建合约测试（FR-020 强制、tasks.md 将以 TDD 顺序组织）

**原则 IX 冲突处置（历史记录，已完成）**:

本 Feature 的 4 个澄清决策（Big Bang、无兼容、404+ENDPOINT_RETIRED、success 布尔位信封）在首次产出 plan.md 时与章程 v1.3.1 存在 Critical 冲突（响应体格式 + 错误响应映射两段）。经决策采纳「方案 A：先修订章程」，已于 **2026-04-27** 完成：

1. ✅ 通过 `/speckit.constitution` 将章程从 v1.3.1 升级至 **v1.4.0（MINOR）**
2. ✅ 原则 IX 中「响应体格式」段重写为顶层 `success` 布尔位互斥信封
3. ✅ 原则 IX 中「错误响应映射」段重写为 `AppException` + 集中化 `ErrorCode` + 全局异常处理器
4. ✅ 原则 IX 新增「已下线接口台账」段，把 `ENDPOINT_RETIRED` 哨兵路由列为强制要求
5. ✅ `.specify/templates/plan-template.md` 的「API 接口规范验证要点」同步到 v1.4.0
6. ✅ 章程顶部「同步影响报告」HTML 注释已更新，登记所有模板同步状态

**结果**：上一节的 ❌ 全部翻转为 ✅；Phase 2（`/speckit.tasks`）阻塞解除。

## 项目结构

### 文档(此功能)

```
specs/017-api-standardization/
├── plan.md              # 本文件
├── spec.md              # 功能规范（已完成，含 4 项澄清）
├── research.md          # 阶段 0 研究：全量路由盘点 + 错误码清点 + 下线决策验证
├── data-model.md        # 阶段 1 设计：Pydantic v2 信封模型 + ErrorCode 枚举 + RetirementLedger 结构
├── quickstart.md        # 阶段 1 产出：新成员按指南实现一个"符合新信封"示例路由 + 合约测试
├── contracts/
│   ├── response-envelope.schema.json    # JSON Schema：成功/错误信封（对应 OpenAPI components）
│   ├── error-codes.md                   # 错误码清单（代码→HTTP 状态→默认消息→触发场景）
│   └── retirement-ledger.md             # 已下线接口台账（旧路径→successor→语义差异）
└── tasks.md             # 阶段 2 产出（由 /speckit.tasks 生成，非本命令产生）
```

### 源代码(仓库根目录)

本项目为**单一后端服务**，采用宪法「附加约束」中的「标准后端」路径约定（`src/`、`tests/`）。

```
src/
├── api/
│   ├── main.py                    # [修改] app.include_router 统一拼装 /api/v1 前缀；注册全局异常处理器
│   ├── errors.py                  # [新增] ErrorCode 枚举 + AppException 基类 + exception_to_response handler
│   ├── schemas/
│   │   ├── envelope.py            # [新增] ResponseEnvelope[T] / ErrorBody / PaginationMeta / RetiredErrorDetails
│   │   ├── classification.py      # [修改] response_model 改为 ResponseEnvelope[ClassificationOut]
│   │   ├── classification_task.py # [修改] 同上
│   │   ├── coach.py               # [修改] 同上
│   │   ├── diagnosis_task.py      # [修改] 同上
│   │   ├── extraction_job.py      # [修改] 同上
│   │   ├── kb_extraction_task.py  # [修改] 同上
│   │   ├── knowledge_base.py      # [修改] 同上
│   │   ├── preprocessing.py       # [修改] 同上
│   │   ├── task.py                # [修改] 同上（含 GET /tasks/{task_id}/result 内层业务字段保持不变）
│   │   ├── task_submit.py         # [修改] 同上
│   │   ├── teaching_tip.py        # [修改] 同上
│   │   └── video_classification.py # [修改] 同上
│   └── routers/
│       ├── _retired.py            # [新增] 哨兵路由：按 RetirementLedger 注册 6 条 410-风格的 404 路由
│       ├── admin.py               # [修改] response_model + 异常抛出改用 AppException
│       ├── calibration.py         # [修改] 同上
│       ├── classifications.py     # [修改] 同上
│       ├── coaches.py             # [修改] 同上；原 /api/v1/coaches 前缀风格保持
│       ├── diagnosis.py           # [下线] 改为哨兵：抛 ENDPOINT_RETIRED，successor=/api/v1/tasks/diagnosis
│       ├── extraction_jobs.py     # [修改] 同上
│       ├── knowledge_base.py      # [修改] 同上
│       ├── standards.py           # [修改] 同上
│       ├── task_channels.py       # [修改] 同上
│       ├── tasks.py               # [修改] 大文件（54.92 KB）内下线 expert-video/athlete-video 端点
│       ├── teaching_tips.py       # [修改] 同上
│       ├── video_preprocessing.py # [修改] 同上
│       └── videos.py              # [下线] 下线 /videos/classifications*、/videos/classifications/refresh；保留纯视频相关接口（若无则整个文件改为哨兵集合）
│
├── services/                      # [不改] 业务逻辑层，本 Feature 不动
├── workers/                       # [不改] Celery 任务层，本 Feature 不动
├── models/                        # [不改] 数据库模型，本 Feature 不动
└── db/                            # [不改]

tests/
├── contract/                      # [全量重写断言] 所有现有契约测试的顶层结构断言改为 body["success"] + body["data"] / body["error"]
│   ├── test_envelope_contract.py  # [新增] 成功/错误信封的通用契约测试（抽样 3 组路由验证泛型解析器）
│   ├── test_retirement_contract.py # [新增] 6 条已下线接口的 404+ENDPOINT_RETIRED 断言
│   └── test_error_codes_contract.py # [新增] ErrorCode 枚举中每个值至少有一条路由触发
├── integration/                   # [修改断言] 跟随信封变化
└── unit/
    └── api/
        ├── test_envelope.py       # [新增] ResponseEnvelope Pydantic 模型单测
        └── test_errors.py         # [新增] ErrorCode + exception_to_response 单测
```

**结构决策**:
- 采用**「选项 1: 单一项目(默认)」** 后端结构，遵循 `src/api/{routers,schemas,errors}` 的现有分层
- **不创建** `frontend/`、`web/` 等目录（宪法「附加约束」明确排除）
- 新增的 `envelope.py`、`errors.py`、`_retired.py` 均落在 `src/api/` 下，不污染 services / models
- 14 个路由文件全部在本 Feature 改造，**不按资源分批**（Big Bang 决策）

### 依赖的外部系统

本 Feature 为纯接口层重构，**无新增外部依赖**。沿用现有：
- FastAPI 全局 `exception_handler(Exception)` 用于兜底 `INTERNAL_ERROR`
- Pydantic v2 `BaseModel` + `Generic[T]` 支持泛型信封
- Celery 队列布局、COS SDK、LLM 客户端、Whisper 推理管道**一律不动**

## 阶段产物引用

- **Phase 0 输出**: [research.md](./research.md)（全量路由盘点表 / 错误码现状清单 / 下线接口语义差异确认）
- **Phase 1 输出**: [data-model.md](./data-model.md) + [contracts/](./contracts/) + [quickstart.md](./quickstart.md)
- **Phase 0.5（章程修订）**: 由 `/speckit.constitution` 产出章程 v1.4.0，非本目录文件
- **Phase 2 输出**: [tasks.md](./tasks.md)（由 `/speckit.tasks` 生成）

## 复杂度跟踪

> 依据章程原则 IV（简洁性与 YAGNI），下表登记本 Feature 相对"最简设计"的复杂性增量。

| 违规/复杂性增量 | 为什么需要 | 拒绝更简单替代方案的原因 |
|-----------|------------|-------------------------------------|
| 泛型 `ResponseEnvelope[T]` 而非裸 `dict` | OpenAPI 文档（FR-019、SC-009）需能为每个 `data` 字段生成准确 schema；裸 dict 会让 `/openapi.json` 全部显示 `additionalProperties: true`，调用方无法做类型推导 | 朴素方案（每个路由手写 `{success, data: XOut}` response_model）→ 每个路由重复 2 行模板代码、新增接口忘写就不合规，维护成本长期滚雪球；泛型方案是"一次成本、全局复用" |
| 保留哨兵路由而非物理删除 | 删除后 FastAPI 默认 404 的 `{"detail": "Not Found"}` 与业务下线无法区分；哨兵路由可在 `error.details.successor` 中指出替代路径，对前端更友好（SC-005） | 物理删除方案一旦前端或外部脚本仍调用旧路径，只能看到 `detail:Not Found`，排障成本高，且未来无法审计"这个路径何时下过线" |
| 原则 IX 冲突需要先修订章程 | 章程条款滞后于本 Feature 的新规范需求，方案 A（修订章程）是唯一能让章程继续作为"活文档"的选项 | 方案 B（例外挂账）会让系统 55+ 接口与章程不一致，等于废弃原则 IX；方案 C（放弃决策）与用户明确指令冲突 |
| 错误码枚举集中到单一文件 | FR-015、SC-004 强制；否则后续新增 Feature 会继续散落字符串字面量 | 不集中的话，CI 无法扫描"裸字符串错误码"，SC-004 变成不可验证指标 |
