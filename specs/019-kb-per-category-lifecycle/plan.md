# 实施计划: 按技术类别独立管理知识库 / 标准 / 教学提示生命周期

**分支**: `019-kb-per-category-lifecycle` | **日期**: 2026-04-30 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/019-kb-per-category-lifecycle/spec.md` 的功能规范

## 摘要

将 `tech_knowledge_bases` 的主键语义从"全局单 active 版本"重构为"**per-(tech_category, version) 独立生命周期**"，使审批、构建标准、生成教学提示三项动作天然按技术类别隔离，消除"批准正手攻球误伤反手拉 active"这类跨类别副作用。技术路径采用"**系统未上线、drop & recreate**"策略（澄清决议 Q5），通过单个 Alembic 迁移 `0017` 清理并重建 KB 相关 6 张表的 FK 列，API 契约按章程 v1.4.0 统一信封与错误码规范暴露 per-category 的查询 / 审批 / 构建能力。TeachingTip 与 KB 绑同生命周期（tips 行内 FK → `(tech_category, kb_version)`），无需独立审批入口。tech_standards build 强制按单类别触发，移除"不传就全量"的旧路径。

## 技术背景

**语言/版本**: Python 3.11（项目章程附加约束；沿用现有虚拟环境 `/opt/conda/envs/coaching/bin/python3.11`）
**主要依赖**: FastAPI + SQLAlchemy 2.0（async）+ Alembic + Pydantic v2 + Celery + Redis + PostgreSQL 驱动 `asyncpg`；无新增依赖
**存储**: PostgreSQL 15+（`tech_knowledge_bases` / `tech_standards`<sup>＋新增 `source_fingerprint CHAR(64)` 列 + 局部唯一索引 `uq_ts_fingerprint_per_category`，用于 FR-019 幂等检查</sup> / `teaching_tips` / `expert_tech_points` / `extraction_jobs` / `analysis_tasks` / `reference_video` / `skill_execution` / `athlete_motion_analysis`）
**测试**: `pytest` + `pytest-asyncio`；`tests/contract/`（API 合约）+ `tests/integration/`（迁移与 E2E）+ `tests/unit/`（service 逻辑分支）
**目标平台**: Linux x86_64 后端服务（uvicorn API + 5 Celery Worker + 1 Beat）
**项目类型**: 单体 Python 后端（本项目 `src/` + `tests/` 结构；禁止 frontend/ 目录——章程附加约束）
**性能目标**:
- KB 列表接口 P95 ≤ 300 ms（SC-002）
- standards build 端到端 ≤ 10 s（SC-004）
- `NO_ACTIVE_KB_FOR_CATEGORY` 错误返回 ≤ 200 ms（SC-005）
**约束条件**:
- 系统未上线 ⇒ **显式 `drop_constraint` + `drop_table` 路径**重建（禁用 `DROP ... CASCADE`，遵循 FR-025；保留 DDL 可审计性），无兼容包袱（澄清决议 Q5）
- 不新增 Celery 队列 / Worker / 周期任务（FR-029）
- 不新增 `business_step`（FR-030；沿用 Feature-018 已定义的 `kb_version_activate` + `build_standards`）
- 新增错误码必须同步 `src/api/errors.py` 三张映射表 + `docs/business-workflow.md` § 7.4
**规模/范围**:
- 21 类 `TECH_CATEGORIES`，每类别独立版本池（预计每类别 5–20 个版本/年）
- `tech_knowledge_bases` 表行数上限预计 < 1 万
- 本功能改动覆盖 6 张表的 schema / 8 个 API 端点 / 3 个 service 模块 / ≥5 个合约测试

## 章程检查

*门控: 必须在阶段 0 研究前通过. 阶段 1 设计后重新检查.*

**章程合规验证**:
- ✅ 规范包含量化精准度指标（原则 VIII）：SC-001～SC-007 全部可度量（P95/P99/行数/秒数均有阈值；精准度约束由 `expert_tech_points.extraction_confidence` 沿用 Feature-002 既有阈值≥0.7，本功能不动）
- ✅ 无前端实现任务混入范围（附加约束）：仅涉及后端 REST API + SQL schema + Celery 无变动
- ✅ 涉及 AI 模型的功能满足原则 VI：本功能不改 AI 模型推理链路；仅涉及模型产物的存储语义
- ✅ 涉及用户数据的功能满足原则 VII：本功能不采集用户视频数据；仅重组既有专家数据的组织方式
- ✅ API 接口设计符合原则 IX（统一信封 v1.4.0）：所有新接口使用 `SuccessEnvelope[T]` / `ok()` / `page()` 构造器 + 分页 `page/page_size` + 错误码枚举；详见 contracts/
- ✅ 功能与业务流程对齐符合原则 X：spec.md「业务阶段映射」已声明 STANDARDIZATION 阶段 + 既有 `kb_version_activate` / `build_standards` 两步骤；不新增步骤；章程级约束双向同步项（§ 4.2 单 active 措辞 + § 7.4 错误码 4 项）将在实现阶段通过 refresh-docs 生效

**API 接口规范验证要点（原则 IX，v1.4.0 统一信封）**:
- ✅ 版本前缀统一使用 `/api/v1/`（沿用 `knowledge_base.py` / `standards.py` / `extraction_jobs.py` 三个已存在资源，无新增资源文件）
- ✅ 路由按资源划分：KB 路径归 `knowledge_base.py`、build 归 `standards.py`、反查归 `extraction_jobs.py`、tips 归 `teaching_tips.py`
- ✅ 分页参数统一：`GET /knowledge-base/versions` 采用 `page + page_size`，越界返回 400 + `INVALID_PAGE_SIZE`（FR-013）
- ✅ 响应体统一信封：所有列表 / 详情 / approve / build 响应 MUST 通过 `SuccessEnvelope[T]` + `ok()` / `page()` 构造
- ✅ 分层职责：路由层仅参数校验；service 层（`knowledge_base_svc.py` / `tech_standard_builder.py` / 新增 `teaching_tip_svc.py`）承载业务逻辑
- ✅ 错误响应统一：新增 4 个错误码（`KB_CONFLICT_UNRESOLVED` / `KB_EMPTY_POINTS` / `NO_ACTIVE_KB_FOR_CATEGORY` / `STANDARD_ALREADY_UP_TO_DATE`）登记到 `src/api/errors.py` 三张映射表
- ✅ 错误码集中化：同步更新 `contracts/error-codes.md`（本 Feature 下）
- ✅ 已下线接口：本 Feature 下线 **2 条老路径**（由新复合主键路径替代）—— `POST /versions/{version}/approve`（单列 version）与 `GET /versions/{version}` / `GET /versions`（单列 version 列表/详情），均在 T020 / T027 保留 `ENDPOINT_RETIRED` 哨兵 + 双份台账（`_retired.py::RETIREMENT_LEDGER` + `contracts/retirement-ledger.md`，后者在 T042 落地），符合原则 IX；此外 `POST /standards/build` 的旧"不传 tech_category 就全量"路径变成 422 拒绝，不算下线（仍是同一路径，行为变严格）
- ✅ 合约测试前置：所有新 API 先写 `tests/contract/test_kb_*.py` 再写实现（tasks.md 阶段会排序）

**业务流程对齐验证要点（原则 X，v1.5.0）**:
- ✅ 权威参考 `docs/business-workflow.md` 已审阅
- ✅ spec.md 含「业务阶段映射」：STANDARDIZATION / `kb_version_activate + build_standards` / DoD 引用 § 2 / 可观测锚点 § 7.1-7.3
- ✅ 章程级约束双向同步项（plan 阶段承诺在实现期完成，由 T040 `/skills refresh-docs` 作为 T048 合并前置一次性刷新）:
  - § 4.2 单 active 措辞改为 per-category
  - § 4.3 状态机图注释补充"作用域 = 单 tech_category"
  - § 7.2 步骤级指标 tag 新增 `tech_category` 维度
  - § 7.4 错误码表增 4 项（`KB_CONFLICT_UNRESOLVED` / `KB_EMPTY_POINTS` / `NO_ACTIVE_KB_FOR_CATEGORY` / `STANDARD_ALREADY_UP_TO_DATE`）
- ✅ 优化活动不涉及三种杠杆：本功能是语义重构而非性能优化，不触发 § 9 杠杆选择约束
- ✅ 回滚剧本：spec.md 已声明 low-risk + `alembic downgrade -1` + `system-init` skill；系统未上线无需新增 § 10 剧本项

**判决**: 门控通过。无违规，无需复杂度跟踪。

## 项目结构

### 文档(此功能)

```
specs/019-kb-per-category-lifecycle/
├── plan.md              # 此文件
├── spec.md              # 已完成
├── research.md          # 阶段 0 输出
├── data-model.md        # 阶段 1 输出
├── quickstart.md        # 阶段 1 输出
├── contracts/           # 阶段 1 输出：5 个 yaml + error-codes.md
│   ├── kb-versions-list.yaml
│   ├── kb-version-detail.yaml
│   ├── kb-version-approve.yaml
│   ├── standards-build.yaml
│   ├── extraction-job-detail.yaml
│   └── error-codes.md
├── checklists/
│   └── requirements.md  # 已完成
└── tasks.md             # 阶段 2 输出 (/speckit.tasks 命令)
```

### 源代码(仓库根目录)

```
src/
├── db/migrations/versions/
│   └── 0017_kb_per_category_redesign.py        # 新增：drop & recreate
├── models/
│   ├── tech_knowledge_base.py                  # 重构：复合主键 + Integer version
│   ├── teaching_tip.py                         # 重构：加 tech_category / kb_ref / status
│   ├── expert_tech_point.py                    # 微调：拆 kb_version FK 为复合键
│   ├── analysis_task.py                        # 微调：knowledge_base_version FK 重构
│   ├── athlete_motion_analysis.py              # 微调：同上
│   ├── reference_video.py                      # 微调：同上
│   └── skill_execution.py                      # 微调：同上
├── api/
│   ├── errors.py                               # 新增 4 个 ErrorCode + 映射表登记
│   ├── schemas/
│   │   ├── knowledge_base.py                   # 重构：响应 schema per-category
│   │   ├── teaching_tip.py                     # 重构：增 tech_category/status 字段
│   │   └── extraction_job.py                   # 微调：加 output_kbs 字段
│   └── routers/
│       ├── knowledge_base.py                   # 重构：复合主键路由；approve 签名变
│       ├── standards.py                        # 重构：build tech_category 必填
│       ├── extraction_jobs.py                  # 微调：详情 + output_kbs
│       └── teaching_tips.py                    # 微调：list 默认过滤 active
├── services/
│   ├── knowledge_base_svc.py                   # 重构：approve(tech_category, version)
│   ├── tech_standard_builder.py                # 重构：强制 tech_category 入参
│   └── teaching_tip_svc.py                     # 新增：联动 approve + 幂等归档
└── workers/
    └── kb_extraction_pipeline/
        └── step_executors/
            └── persist_kb.py                   # 微调：按 tech_category 分组产出 N 条 KB（实际路径以 T033pre 探查结果为准）

tests/
├── contract/
│   ├── test_kb_versions_list.py                # 新增：list 契约
│   ├── test_kb_version_detail.py               # 新增：detail 契约
│   ├── test_kb_version_approve.py              # 新增：approve 契约（+错误码）
│   ├── test_standards_build_per_category.py    # 新增：build 契约
│   └── test_extraction_job_detail.py           # 新增：output_kbs 契约
├── integration/
│   └── test_0017_migration_roundtrip.py        # 新增：upgrade/downgrade 3 次幂等
└── unit/
    ├── test_approve_version_branches.py        # 新增：6 条分支（故事 1 覆盖）
    ├── test_tech_standard_builder_per_category.py  # 新增：单类别 / 幂等 / 无 active
    └── test_teaching_tip_svc_lifecycle.py      # 新增：KB approve 联动 tips
```

**结构决策**: 采用章程约定的"标准后端"单体结构（`src/` + `tests/`）。Feature-019 不新增顶级目录，所有变更落到现有模块上；路由与 service 均按既有资源归属原则不拆分。`workers/kb_extraction_pipeline/step_executors/persist_kb.py` 是本功能唯一涉及 DAG 的改动点（让 persist 步按 tech_category 分组产出 N 条 KB 记录）。

## 复杂度跟踪

*章程检查无违规，本节保持为空（按模板规则不列出任何行）。*

| 违规 | 为什么需要 | 拒绝更简单替代方案的原因 |
|-----------|------------|-------------------------------------|
| （无） | — | — |
