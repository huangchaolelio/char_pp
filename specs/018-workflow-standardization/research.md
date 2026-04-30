# 研究: 处理流程规范化（Workflow Standardization） — Feature-018

**阶段**: 0（Outline & Research）
**输入**: [spec.md](./spec.md) Clarifications Q1–Q5 + 技术背景 + 章程 v1.5.0
**产出**: 消解所有技术选型与风险未知项；每项以 **Decision / Rationale / Alternatives** 三元组呈现。

> 本 Feature 的 `NEEDS CLARIFICATION` 已在 `/speckit.clarify` 阶段（Q1–Q5）全部消解；本研究节聚焦"最佳实践 + 风险摸底"七个主题。

---

## R1. SQLAlchemy `before_insert` 事件钩子与 `Enum` 默认值的协同

**Decision**: 采用 `sqlalchemy.event.listen(Model, "before_insert", _fn)` 注册钩子；钩子内通过 `inspect(instance).attrs.business_phase.history.has_changes()` 区分"调用方显式传入"与"默认 None"两种情形；集中注册于 `src/models/_phase_step_hook.py::register_phase_step_hooks(engine_or_metadata)`，由 `src/db/session.py` 的 `Base.metadata` 创建时一次性调用。

**Rationale**:
- `before_insert` 在 `session.flush()` 之前执行，早于列级 `NOT NULL` 检查，保证派生生效
- 使用 `history.has_changes()` 而非 `is None` 判断，能区分"调用方显式传 None"（视为错误，抛 `PHASE_STEP_UNMAPPED`）与"完全未赋值"（走派生表）
- 单模块集中注册避免散落四处的 `@event.listens_for` 装饰器导致的"忘注册"风险（原则 IV 简洁优先）
- 钩子抛 `ValueError("PHASE_STEP_UNMAPPED: ...")` 与列级 `NOT NULL` 兜底形成双层防御（Clarification Q4 决议）

**Alternatives**:
- ❌ SQLAlchemy `column_property(default=...)`: 只能给字面量，不能动态派生
- ❌ `@validates` 装饰器: 字段赋值时触发，对"完全未赋值"场景不触发
- ❌ 在每个 service 函数入口手填: FR-003 已否决（"衍生写入"路径多，易漏）

---

## R2. PostgreSQL `pg_class.reltuples` 估算稳定性

**Decision**: 降级阈值判断使用 `SELECT reltuples::bigint FROM pg_class WHERE relname = 'analysis_tasks'`；配合 `SHOW autovacuum_analyze_scale_factor`（默认 0.1）+ 项目定时 `VACUUM ANALYZE`（建议每日 03:00，由现有 `cleanup_expired_tasks` 周期任务附带触发）保持估算偏差 ≤ 10%。降级阈值上浮到 **110 万**（实际 ≤ 100 万行区间）与 **110 万 ~ 950 万**（降级档区间），留 10% 偏差余量。

**Rationale**:
- `COUNT(*)` 自身在 500 万行量级耗时 ~300ms，与 FR-007 的 P95 ≤ 500ms 预算冲突
- `reltuples` 读 pg_class 缓存，纳秒级；偏差主要来自 autovacuum 滞后
- 本 Feature SC-001 已保障 `business_phase` NOT NULL 即随 `created_at` 并写入，不会因插入高峰恶化估算偏差
- 偏差余量 10% 是 Postgres 官方文档 `reltuples` 精度说明的保守下限

**Alternatives**:
- ❌ `SELECT COUNT(*) FROM analysis_tasks`: 性能不达标
- ❌ 额外维护 `row_count_cache` 表 + 触发器: 违背 YAGNI（原则 IV），且高写入并发下触发器本身会成为瓶颈
- ⚠️ `pg_stat_user_tables.n_live_tup`: 与 reltuples 来源相同但更新略滞后，未选用

---

## R3. Alembic「ADD COLUMN → 回填 → ALTER NOT NULL」原子事务策略

**Decision**: 迁移 `0016_business_phase_step.py`（当前 head 为 `0015_kb_audit_and_expand_action_types`，本迁移 `revision="0016"` / `down_revision="0015"`）分三步：
1. `ALTER TABLE ... ADD COLUMN business_phase business_phase_enum NULL` + `business_step VARCHAR(64) NULL`（瞬时，非阻塞）
2. 单条 `UPDATE ... SET business_phase=..., business_step=... WHERE ...`（基于 FR-002 的 `CASE WHEN task_type = ...`），**分批次每批 5 万行 + `VACUUM`**（若行数 > 50 万）
3. `ALTER TABLE ... ALTER COLUMN business_phase SET NOT NULL`（短锁）

**Rationale**:
- 项目当前 `analysis_tasks` 行数（生产态约 10 万 ~ 50 万）远低于 Postgres 在线 DDL 的阻塞阈值，单事务可完成；`extraction_jobs` / `video_preprocessing_jobs` / `tech_knowledge_bases` 行数更少
- 未来若规模上升到 ≥ 500 万行，再走 `CREATE TABLE AS ... + SWAP` 的重量级方案（届时另起 Feature）
- `idx_analysis_tasks_channel_counting(task_type, status)` 已存在，回填 UPDATE 的 `WHERE task_type=...` 子句直接命中索引
- 本 Feature 仅新增两个索引：`idx_analysis_tasks_phase_step(business_phase, business_step)` 用于 `?business_phase=` 筛选；`idx_extraction_jobs_phase(business_phase)` 用于跨 `extraction_jobs` / `analysis_tasks` JOIN 聚合

**Alternatives**:
- ❌ `ADD COLUMN ... NOT NULL DEFAULT 'TRAINING'`: Postgres 11+ 虽支持瞬时默认，但回填历史行到"正确映射"需要额外 UPDATE，不如直接分步清晰
- ❌ `gh-ost`/`pg-osc` 在线迁移工具: 项目未部署，YAGNI

---

## R4. 漂移扫描的 markdown 解析策略

**Decision**: 基于正则 + 锚定标题切片（`^#{2,3} § 7\.4\s`）实现"章节定位器"，单文件 `mistletoe` 级别的 AST 不引入；对表格内容采用 `re.findall(r'^\|\s*`?([\w_-]+)`?\s*\|', section_body, re.M)` 抽取第一列。

**Rationale**:
- `docs/business-workflow.md` 为项目唯一扫描目标，结构稳定（§ 1 ~ § 11），非多源动态
- 简单正则易于单测覆盖（`tests/unit/audit/test_workflow_drift_parser.py`）
- 引入 `markdown-it-py` 会带来 `mdit-py-plugins` 等传递依赖，违反原则 IV（YAGNI）
- 若未来文档结构重大改版（超过 3 次误报/月），再升级至 AST 方案（延迟决策）

**Alternatives**:
- ❌ `mistletoe` 或 `markdown-it-py` AST: 过度设计
- ❌ 让业务流程文档改写为 YAML 清单 + markdown 渲染: 破坏人类可读性（该文档日常由运营直接阅读）

---

## R5. `config/optimization_levers.yml` 的敏感位标记语义

**Decision**: YAML 条目含 `sensitive: true | false`（默认 false）字段；加载时对 `sensitive: true` 条目：
- 响应中 `current_value` 字段**完全省略**（不返回空字符串、不返回 `***`）
- 新增 `is_configured: bool` 字段，通过 `bool(os.environ.get(key))` 或 `settings.<attr> is not None` 判定
- 加载器使用 `src/config/settings.py::Settings` 的 `SecretStr` 字段作为判定口径（已存在）

**Rationale**:
- 与 Feature-017 `ADMIN_TOKEN_NOT_CONFIGURED` 的 fail-safe 原则一致：宁可不可见，不可泄露
- `is_configured` 足以支撑运维"这个值是否生效"的判断，无需暴露内容
- 默认 `sensitive: false` 让研发新增杠杆时不必每次都写，降低心智成本

**当前已知敏感键清单**（YAML 初版）:
- `VENUS_TOKEN` / `OPENAI_API_KEY` / `COS_SECRET_KEY` / `POSTGRES_PASSWORD`

**Alternatives**:
- ❌ 回传 `"***"` 占位: 无意义且可能被误解析为"已配置"
- ❌ 按 key 名正则自动识别敏感（`.*TOKEN|.*KEY|.*PASSWORD`）: 易漏（如 `WEBHOOK_SECRET`）、易误伤（如 `KB_VERSION_KEY`）

---

## R6. FastAPI 查询参数级联校验

**Decision**: 在 `src/api/routers/tasks.py::list_tasks` 的 `Depends` 依赖函数 `_validate_phase_step_task_type_combo()` 中集中校验；非法组合抛 `AppException(INVALID_PHASE_STEP_COMBO, details={"conflict": "phase_step_task_type_mismatch", "hint": "..."})`。**新增错误码** `INVALID_PHASE_STEP_COMBO`（400）。

校验矩阵（三参数联合）:
| `business_phase` | `business_step` | `task_type` | 结果 |
|-----------------|-----------------|-------------|------|
| `TRAINING` | `extract_kb` | `kb_extraction` | ✅ 允许 |
| `TRAINING` | `extract_kb` | `athlete_diagnosis` | ❌ 400 |
| `INFERENCE` | — | `kb_extraction` | ❌ 400 |
| — | `scan_cos_videos` | `athlete_diagnosis` | ❌ 400 |
| `TRAINING` | — | `video_classification` | ✅ 允许（`scan_cos_videos` / `classify_video` 均可） |

**Rationale**:
- 路由层的 `Depends` 是校验级联逻辑的天然落位（原则 IX「路由层仅做参数校验与响应组装」）
- 单独错误码便于监控告警与合约测试断言
- 硬失败（而非静默返回空列表）符合边界情况条目 6

**Alternatives**:
- ❌ service 层校验: 违背分层职责
- ❌ Pydantic `@model_validator(mode="after")`: 查询参数不走 Pydantic model，FastAPI 用 `Query()` 装饰器

---

## R7. CI `--changed-only` 模式的"涉及清单"计算算法

**Decision**: 扫描脚本接口与算法如下:

```bash
python -m scripts.audit.workflow_drift --changed-only [--commit-range=origin/master...HEAD]
python -m scripts.audit.workflow_drift --full

python -m scripts.audit.spec_compliance --changed-only [--commit-range=...]
python -m scripts.audit.spec_compliance --full
```

- `--changed-only` 默认用 `git diff --name-only origin/master...HEAD` 获取变更文件列表
- 代码侧命中: 文件路径属于 `src/services/kb_extraction_pipeline/error_codes.py` / `src/models/analysis_task.py` / `src/models/extraction_job.py` / `src/models/tech_knowledge_base.py` / `src/services/diagnosis_scorer.py` / `src/db/migrations/versions/*task_channel*` 任一白名单 → 触发对应"清单片段"的扫描
- 文档侧命中: 文件路径 = `docs/business-workflow.md` → 触发该文档所有 anchored-section 的扫描（因无法精确 diff 到 § 编号粒度）
- 无命中 → 脚本 `exit 0` 且输出 `scope: changed-only, no target in diff`

**Rationale**:
- `origin/master...HEAD` 三点语法取"合并基"，语义等同于 PR 的改动集
- 代码侧白名单硬编码在脚本内（原则 IV），不做反射扫描
- 文档侧整体扫描成本低（单文件 < 1000 行），不需要精确到 § 粒度

**退出码与输出契约**（FR-009 细化）:
- 无差异 → `exit 0`，stdout 打印 `OK: scanned={n_sections}, mode={full|changed-only}`
- 有差异 → `exit 1`，每行 `DRIFT: <kind> <identifier> code_side=<v> doc_side=<v>`；末行汇总 `SUMMARY: drift_count=<n>`
- `spec_compliance` 类似，失败行格式 `MISSING_SECTION: <feature_id>/spec.md <section_name>`

**Alternatives**:
- ❌ 基于 `pathspec` glob 的动态发现: 脚本复杂度激增
- ❌ 借用 `pre-commit` 插件链: 引入新依赖，违背 YAGNI

---

## 汇总

| # | 主题 | 决策要点 |
|---|------|---------|
| R1 | ORM 钩子 | 集中注册于 `_phase_step_hook.py` + `has_changes()` 判定显式传入 |
| R2 | reltuples 估算 | 降级阈值上浮 10% 吸收偏差；依赖现有 autovacuum + 定时 VACUUM |
| R3 | Alembic 策略 | 三步原子迁移；新增 2 个索引；不引入重量级迁移工具 |
| R4 | markdown 解析 | 正则 + anchored-section；不引入 AST 库 |
| R5 | YAML 敏感位 | `sensitive: true` 完全省略 `current_value` + 返回 `is_configured` |
| R6 | 查询参数校验 | 路由层 `Depends` 集中校验；新增 `INVALID_PHASE_STEP_COMBO`（400） |
| R7 | CI 扫描算法 | `git diff` 三点语法 + 代码侧路径白名单 + 文档整体扫描 |

所有研究项决策均**就地可实施**，无残留 `NEEDS CLARIFICATION`。可进入阶段 1。
