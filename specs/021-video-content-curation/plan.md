# 实施计划: 教练视频内容清洗与有效片段筛选规范

**分支**: `021-video-content-curation` | **日期**: 2026-05-18 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/021-video-content-curation/spec.md` 的功能规范

## 摘要

在 TRAINING 阶段 `classify_video`（步骤 3）与 `extract_kb`（步骤 4）之间，**新增独立的"内容清洗"步骤** `curate_segments`：以 F-016 视频预处理产物中的"分段"为最小判定粒度，通过"规则优先 + LLM 兜底"两层骨架（规则路看关键词 / 时长 / 教练主导 / 主题相关性，得分明确直接定案；落入 `(0.3, 0.7)` 模糊区间的分段才调一次 LLM 复核）输出 `auto_decision / validity_score / rejection_reason` + 视频级摘要（`accepted_duration_ratio` / `low_quality` 等）。下游 `extract_kb` 在本 feature 后**强制只读 `effective_decision = override_decision ?? auto_decision == 'accepted'` 的分段集合**；视频级 `accepted_duration_ratio == 0` 触发 `LOW_QUALITY_SKIP` 业务短路；尚未跑过清洗的视频提交 KB 抽取一律以 `CURATION_REQUIRED` 拒绝。运营可逐分段人工覆盖，覆盖后视频级摘要自动重算，但**不自动级联触发已有 KB 作业重抽**——监控暴露 `kb_stale_after_override` 提示，运营按需 `POST /extraction-jobs/{id}/rerun` 显式重跑。

**技术方法**：

- 实体上**新增 2 张表**——`video_curation_jobs`（作业级摘要）+ `video_curation_segment_results`（分段判定 + 覆盖留痕同行扩展）；不改 `coach_video_classifications` 列含义，仅在其上加一个查询友好的 `last_curation_job_id`（可空）+ `low_quality` 派生字段。
- 服务上**新增 1 个清洗 service** `services/curation/curation_service.py` + 1 个 **规范加载器** `services/curation/rubric_loader.py`（启动期 + 任务排队前各做一次 schema 校验）+ 1 个 **决策器骨架** `services/curation/decision_engine.py`（规则路 + LLM 兜底两层装配）。
- 任务上**新增 1 个 Celery task** `workers/curation_task.py::curate_video`，**复用 `default` 队列**（不新增队列、不新增 worker）。
- 路由上**新增 4 个 endpoint** + **扩展 1 个**：`POST /tasks/curation`（单 + 批量提交，统一走 `tasks.py` 已有调度门）、`GET /curation-jobs/{id}`（视频级摘要 + 逐分段判定）、`PATCH /curation-jobs/{id}/segments/{segment_index}`（人工覆盖单分段）、`GET /curation-stats`（P3：按 coach / tech_category / rubric_version 聚合）；扩展 `POST /tasks/kb-extraction` 增加 `CURATION_REQUIRED` / `LOW_QUALITY_SKIP` 前置门。
- 算法上**不引入新模型**：规则路完全用 Python；LLM 路统一走 `src/services/llm_client.py`（Venus 优先 → OpenAI fallback）；转录复用 F-002 预处理已落地的 `transcript.json`。
- 错误码集中登记 `CURATION_*` / `RUBRIC_*` / `LOW_QUALITY_SKIP` / `CURATION_REQUIRED` 共 7 个，同步到 `src/api/errors.py` + `contracts/error-codes.md` + `docs/business-workflow.md § 7.4`（已扩展）。

## 技术背景

**语言/版本**: Python 3.11（`/opt/conda/envs/coaching/bin/python3.11`；章程附加约束"Python 环境隔离"，禁止系统 Python 3.9）
**主要依赖**: FastAPI（API）、SQLAlchemy 2.x Async（ORM）、Alembic（迁移）、Celery 5.x（异步任务）、PyYAML（规范文件加载）、jsonschema（规范文件 schema 校验）、`src/services/llm_client.py`（Venus/OpenAI 兜底）；**不引入新算法依赖**——规则路纯 Python，LLM 调用复用既有客户端
**存储**: PostgreSQL（新增 `video_curation_jobs` + `video_curation_segment_results` 两张表，`coach_video_classifications` 增补 2 列）；Git（清洗规范文件 `src/config/curation_rubric/*.yaml` 与代码同源 — 见 spec Q1 决议）；不写 COS、不写本地磁盘 artifact（规则中间结果与判定理由全部入 DB）
**测试**: pytest + pytest-asyncio + httpx.AsyncClient（路由合约测试）+ pytest-postgresql（迁移测试）+ pytest-mock（LLM client mock）
**目标平台**: Linux 服务器，内部运营环境
**项目类型**: 单一后端项目（无前端交付；附加约束"前端路径 MUST NOT 创建"）
**性能目标**:
- 单条视频清洗任务 p95 ≤ 30 秒（典型 N=20 个分段，规则路本地命中 ≥ 80%、LLM 兜底 ≤ 4 分段 × 5 秒/次）
- 批量提交 10 条视频 p95 ≤ 90 秒（`default` 队列 concurrency=1，串行执行；与 spec SC-006 串行降级阈值对齐：`(10/1) × 单条 p95 × 1.2 ≤ 360 秒`，实测 < 100 秒留有 3 倍余量）
- 规范文件加载（启动期 + 任务排队前）p99 ≤ 50 ms（YAML 解析 + jsonschema 校验，文件 < 10 KB）

**精准度基准**（原则 VIII）：
- 自动清洗在人工标注样本集（≥ 30 条混合内容视频）上：无效分段召回率 ≥ 0.85，有效分段精确率 ≥ 0.85（spec SC-001）
- 清洗后 KB 抽取 LLM 输入 token 量下降 ≥ 30%（spec SC-002）
- 清洗后同 `tech_category` 多视频间技术术语重叠率提升 ≥ 20%（spec SC-003）

**约束条件**:
- 分页遵循章程 v1.4.0：`page` / `page_size`，`page_size ∈ [1,100]`，越界 422 `INVALID_PAGE_SIZE`
- 响应统一 `SuccessEnvelope[T]` 成功信封 / `{success:false,error:{code,message,details}}` 错误信封
- 错误码新增 MUST 同步 4 处：`ErrorCode` 枚举 / `ERROR_STATUS_MAP` / `ERROR_DEFAULT_MESSAGE` / `contracts/error-codes.md` + 业务侧 § 7.4
- 不新增 Celery 队列；复用 `default` 队列与 `scan_cos_videos` / `housekeeping` / `cleanup_*` / `sweep_orphan_jobs` 同列
- 与 Feature-020 运动员侧表 / 路径严格物理隔离：本 feature 一律不读 / 不写 `athlete_video_classifications` / `athletes` / 运动员根路径

**规模/范围**: 单 feature 新增 ≈ 2 张表 + 2 列（`coach_video_classifications` 增补）+ 1 张 ENUM（`task_type` 新增 `video_curation`）+ 5 个 service 模块（rubric_loader / decision_engine / curation_service / segment_text_provider / coach_dominance_detector）+ 2 个路由文件（`curation_jobs.py` 新建 + `tasks.py` 扩展）+ 1 个 Celery task + 1 个 Alembic 迁移（当前 head 为 `0019_add_missing_task_channel_configs`，本次使用 `0020_video_content_curation`）+ 7 个新 `ErrorCode` + 1 份初版规范 `src/config/curation_rubric/v1.yaml`

## 章程检查

*门控: 必须在阶段 0 研究前通过. 阶段 1 设计后重新检查.*

**章程合规验证**（对 v2.0.0）：

- ✅ 规范包含量化精准度指标（原则 VIII）——SC-001 召回率/精确率 ≥ 0.85；SC-002 LLM token 下降 ≥ 30%；SC-003 术语重叠率提升 ≥ 20%；SC-006 批量加速 ≥ 40%；SC-008 不得拉低既有 KB 抽取完成率
- ✅ 无前端实现任务混入范围——本 feature 仅 后端 / 服务 / 路由 / Celery task，零前端（附加约束）
- ✅ 涉及 AI 模型——LLM 复用 `src/services/llm_client.py`（Venus/OpenAI），**不新增模型**；不动 pose_estimator / Whisper；原则 VI 合规
- ✅ 涉及用户数据——清洗只读教练侧已有素材表 + 已有预处理转录文本，**无新增数据采集面**；原则 VII 合规
- ✅ API 接口设计符合原则 IX（v1 前缀 + 资源化路由 + `page/page_size` 分页 + 统一信封 + `AppException` + 错误码集中化 + 合约测试前置 + 下线物理删除）
- ✅ 业务流程对齐符合原则 X：
  - `spec.md` 已含「业务阶段映射」节（所属阶段 = `TRAINING`；所属步骤 = 新增 `curate_segments`，位置在步骤 3 与 4 之间；DoD 引用 § 2 训练行——本 feature 不闭合 DoD，DoD 仍由下游 `extract_kb` 闭合；可观测锚点 § 7.1 / § 7.2 / § 7.4）
  - **新扩展步骤 MUST 先扩 business-workflow.md**：**已执行**——本任务前置已在 § 2 阶段全景图加 `T35` 节点；§ 3.1 八步骤总览插入 `curate_segments` 行（队列=`default`、并发=1、前置=`tech_category`+预处理完成、后置=`extract_kb` 强制门）；§ 3.4 新增"内容清洗契约"小节描述算法骨架 + 规范文件契约 + 双阈值消费门 + 人工覆盖语义；§ 7.4 错误码表追加 7 个 `CURATION_*` / `RUBRIC_*` / `LOW_QUALITY_SKIP` / `CURATION_REQUIRED` 行；§ 8 调度图加 `A35` 节点；§ 10 应急表追加 2 行（清洗误伤 / 清洗结果错误）
  - 队列拓扑变化：**无新增队列**；business-workflow.md § 3.1 队列复用说明已扩展含 Feature-021
  - 状态机枚举变化：`AnalysisTask.task_type` 新增 1 个值 `video_curation`（同步扩 `_phase_step_hook` 派生矩阵 + `_PHASE_STEP_TASK_TYPE_MATRIX` + `_PHASE_TASK_TYPES` + `tasks.py::_VALID_BUSINESS_STEPS` 白名单 4 处）；`pipeline_steps` 不新增枚举
  - 错误码前缀变化：新增 `CURATION_REQUIRED:` / `LOW_QUALITY_SKIP:` / `RUBRIC_INVALID:` / `RUBRIC_VERSION_NOT_FOUND:` / `CURATION_TIMEOUT:` / `CURATION_LLM_UNAVAILABLE:` / `CURATION_RUBRIC_MISMATCH:` 共 7 个；同步登记 4 处
  - 诊断评分公式：**无变化**，本 feature 不触诊断侧
  - 单 active / 冲突门控：**无变化**，本 feature 不触 KB 状态机
- ✅ 优化活动命中 § 9 三种杠杆：本 feature 不是优化活动，是"新增能力 + 强制门"；不需要命中杠杆分类（清洗规范小幅调整时归 § 9"规则/Prompt"杠杆，**已在 § 9 配置范畴内**，无需扩 § 9）
- ✅ 高风险操作引用 § 10 回滚剧本：本 feature 引入"清洗强制前置门"具有中等风险（可能误伤导致 KB 抽取读不到任何分段）；business-workflow.md § 10 已追加 2 行剧本（"清洗规则误伤 → bypass 门 + 回滚 rubric_version" / "清洗结果错误 → 删 job 行重跑"）

**门控结论**：✅ 全部通过，可进入阶段 0。

## 项目结构

### 文档（本功能）

```
specs/021-video-content-curation/
├── plan.md              # 本文件（/speckit.plan 输出）
├── spec.md              # /speckit.specify + /speckit.clarify 产物
├── research.md          # 阶段 0 输出（/speckit.plan）
├── data-model.md        # 阶段 1 输出（/speckit.plan）
├── quickstart.md        # 阶段 1 输出（/speckit.plan）
├── contracts/           # 阶段 1 输出（/speckit.plan）
│   ├── error-codes.md                       # 新增 CURATION_* / RUBRIC_* / LOW_QUALITY_SKIP / CURATION_REQUIRED 登记
│   ├── submit_curation.md                   # POST /api/v1/tasks/curation（单 + 批量）
│   ├── get_curation_job.md                  # GET  /api/v1/curation-jobs/{id}
│   ├── override_curation_segment.md         # PATCH /api/v1/curation-jobs/{id}/segments/{segment_index}
│   ├── curation_stats.md                    # GET  /api/v1/curation-stats（P3）
│   └── kb_extraction_curation_gate.md       # 扩展：POST /api/v1/tasks/kb-extraction 强制门 + 错误码
└── tasks.md             # 阶段 2 输出（/speckit.tasks，本命令不创建）
```

### 源代码（仓库根目录）

采用**选项 1：单一项目**（已删除选项 2/3 占位）：

```
src/
├── api/
│   ├── routers/
│   │   ├── curation_jobs.py                 # 新增：清洗作业查询 + 人工覆盖
│   │   ├── curation_stats.py                # 新增：P3 聚合统计
│   │   ├── tasks.py                         # 扩展：新增 POST /tasks/curation 路径；
│   │   │                                    # 扩展 POST /tasks/kb-extraction 加前置门（CURATION_REQUIRED / LOW_QUALITY_SKIP）
│   │   └── ...                              # 其他路由不动
│   ├── schemas/
│   │   └── curation.py                      # 新增：CurationJobItem / CurationSegmentResult / OverrideRequest / CurationStatsItem
│   └── errors.py                            # 扩展：新增 7 个 ErrorCode + 状态映射 + 默认消息
├── config/
│   └── curation_rubric/                     # 新增：清洗规范文件目录
│       ├── v1.yaml                          # 初版规范（关键词 / 阈值 / 规则开关）
│       └── schema.json                      # 规范文件 jsonschema（与 v1.yaml 同源校验）
├── models/
│   ├── video_curation_job.py                # 新增：VideoCurationJob ORM
│   ├── video_curation_segment_result.py     # 新增：VideoCurationSegmentResult ORM（含 override_* 列同行扩展）
│   ├── coach_video_classification.py        # 扩展：增 last_curation_job_id (FK, nullable) + low_quality (bool, nullable)
│   └── _phase_step_hook.py                  # 扩展：派生矩阵增 video_curation → (TRAINING, curate_segments)
├── services/
│   └── curation/                            # 新增：清洗子包
│       ├── __init__.py
│       ├── rubric_loader.py                 # 加载 + jsonschema 校验 src/config/curation_rubric/vN.yaml；缓存
│       ├── decision_engine.py               # 规则路 + LLM 兜底两层装配；输出 (decision, validity_score, rejection_reason)
│       ├── segment_text_provider.py         # 从预处理 transcript.json 切片到分段对应文本（按 start_ms / end_ms）
│       ├── coach_dominance_detector.py      # 启发式：分段是否以目标教练为主（讲解长度 / 关键词 / 旁观者识别）
│       ├── curation_service.py              # 编排：load → for-each-segment(decide) → aggregate summary → persist
│       └── error_codes.py                   # 私域错误常量（透出到 src/api/errors.py）
├── workers/
│   └── curation_task.py                     # 新增：curate_video Celery task（路由到 default 队列）
├── api/
│   └── tasks.py                             # 已在上方
└── db/migrations/versions/
    └── 0020_video_content_curation.py       # 新增：2 张表 + coach_video_classifications 2 列 + analysis_tasks.task_type ENUM 扩展

tests/
├── contract/
│   ├── test_submit_curation.py
│   ├── test_get_curation_job.py
│   ├── test_override_curation_segment.py
│   ├── test_curation_stats.py
│   └── test_kb_extraction_curation_gate.py            # 验证 CURATION_REQUIRED / LOW_QUALITY_SKIP 双场景
├── integration/
│   ├── test_curation_end_to_end.py                    # 端到端：预处理产物 → 清洗 → 视频级摘要校验
│   ├── test_curation_rubric_versioning.py             # 多版本规范并存 + 版本回查
│   ├── test_kb_extract_consumes_accepted_only.py      # 验证下游只读 accepted 分段（spec SC-008 关键护栏）
│   ├── test_low_quality_skip_path.py                  # accepted_duration_ratio == 0 → KB 业务短路
│   └── test_override_recompute_summary.py             # 覆盖后视频级摘要自动重算 + kb_stale_after_override 提示
└── unit/
    ├── test_rubric_loader.py                          # YAML 加载 + jsonschema 校验 + 缓存
    ├── test_decision_engine_rule_only.py              # 规则路明确得分（≤ 0.3 / ≥ 0.7）直接定案
    ├── test_decision_engine_llm_fallback.py           # 模糊区间 LLM 兜底；LLM 不可用 → uncertain
    ├── test_coach_dominance_detector.py
    ├── test_segment_text_provider.py
    ├── test_curation_service_aggregation.py           # 视频级摘要 accepted_duration_ratio / low_quality 派生
    ├── test_curation_phase_step_hook.py               # _phase_step_hook 新增 1 条映射
    └── test_errors_curation_codes.py                  # 7 个新 ErrorCode 映射单测
```

**结构决策**: 严格沿用现有单一项目布局，**不新建平行目录**。新增的 `services/curation/` 子包遵循已有的 `services/kb_extraction_pipeline/` 编排骨架——子包内分 `*_loader` / `*_engine` / `*_service` / `error_codes.py`，与既有架构惯例同构。规范文件落 `src/config/curation_rubric/`（Q1 决议：与代码同源、git 维护、PR 上线）；不在 `config/` 顶层放，因为 `config/` 是"静态业务字典"位（如 `coach_directory_map.json`），而规范文件具有"运行时强 schema 校验 + 与代码同源 + 启动期加载"的工程属性，归到 `src/config/` 更恰当且与 `src/config/video_classification.yaml` / `src/config/keywords/tech_hint_keywords.json` 一致。

## 复杂度跟踪

本 feature 未引入任何超出章程或规范要求的额外抽象。所有"看似可合并"的模块拆分均由职责边界 + 章程原则 IV "简洁性与 YAGNI"自然约束：

- `rubric_loader` 与 `decision_engine` 拆开 — 因为规范加载是"启动期 + 任务排队前"两次调用，需缓存；决策器是"任务执行期 N 次调用 / 段"，调用频次差 N 个量级
- `segment_text_provider` 与 `coach_dominance_detector` 拆开 — 一个是 I/O（读 transcript JSON），一个是启发式判定（纯计算），混在一起会让单测难拆
- `curation_service` 不内联 task — Celery task 文件只做"任务接收 + service 调度 + 错误码归一"，业务编排归 service，与既有 `kb_extraction_task.py` 同惯例

**回滚策略（中风险档，已在 business-workflow.md § 10 登记）**：

| 风险场景 | 回滚动作 | 影响面 |
|---------|---------|--------|
| 清洗规则误伤导致 KB 抽取读到的有效片段过少 | (1) 临时把 `extract_kb` 的"清洗强制前置门"切到 `bypass`（运营级开关，登记审计）— 实现为 `task_channel_configs.kb_extraction.config_payload.bypass_curation_gate=true`，30 秒 TTL 内生效 (2) 回滚到上一版 `curation_rubric_version`（git revert + 重新部署）(3) 对受影响视频 `force=true` 重跑清洗 | 临时下游退到读全量分段，等价于 Feature-021 之前的行为；不影响 KB 已落地的 expert_tech_points |
| 清洗结果或人工覆盖数据错误 | `DELETE FROM video_curation_segment_results WHERE job_id = ...` + `DELETE FROM video_curation_jobs WHERE id = ...` + 重跑 `POST /tasks/curation`；若已重抽过 KB，运营 `POST /extraction-jobs/{id}/rerun` 显式重跑 | 仅清洗侧 + KB 侧二选一可控重跑，不影响 standards / teaching_tips |
| 迁移失败 | `alembic downgrade 0019_add_missing_task_channel_configs` 回退 2 张表 + 2 列 + 1 个 ENUM 值（PostgreSQL ENUM 删除在 0020 的 downgrade 中以 `ALTER TYPE ... RENAME VALUE` 跳过——只增不减，避免运行中 worker 见到未知枚举） | 可逆；ENUM 值保留是有意为之，避免"应用滚动 ENUM 不一致"窗口 |
| 规范文件 schema 不兼容升级 | 旧规范版本文件保留在 git，按版本号回查；新版本通过 PR 必经 `tests/unit/test_rubric_loader.py::test_v_n_yaml_passes_schema` 强制校验，发布前一定可加载 | 历史结果保留旧版本号；事后审计无歧义 |

`bypass_curation_gate` 开关属于章程 § 9 "运行时参数"杠杆类（`task_channel_configs` 热配置）的合法应用形态；不引入新杠杆类型，符合 § 9 的封闭枚举。
