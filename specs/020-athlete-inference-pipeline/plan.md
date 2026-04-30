# 实施计划: 运动员推理流水线 · COS 扫描 → 预处理 → 姿态提取 → 标准对比 → 改进建议

**分支**: `020-athlete-inference-pipeline` | **日期**: 2026-04-30 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/020-athlete-inference-pipeline/spec.md` 的功能规范

## 摘要

把现有"只服务教练侧 TRAINING 阶段"的三段式链路——**COS 全量扫描 → 视频预处理 → tech_category 分类**——在 INFERENCE 阶段做**一条同构的运动员侧镜像流水线**：运营把运动员原始视频按"根路径 / 运动员目录 / 视频.mp4"上传到独立 COS 根路径 `COS_VIDEO_ALL_ATHLETE`，触发运动员素材扫描 → 自动复用 F-016 预处理管道 → 以素材 ID（而非 `video_storage_uri`）提交诊断；诊断服务加载 **active `tech_standards`** 生成综合得分 + 维度偏差 + 改进建议，落库可反查到 `cos_object_key / preprocessing_job_id / standard_version` 三要素。

**技术方法**：实体/服务/路由三层都**与教练侧严格分叉**（独立表 `athletes` / `athlete_video_classifications`、独立 scanner、独立 router `athlete_classifications.py`），仅在算法底座（TechClassifier 21 类字典、F-016 orchestrator、DiagnosisService 11 步）层面复用。队列拓扑**零新增**——运动员扫描复用 `default`，预处理复用 `preprocessing`，诊断复用 `diagnosis`。错误码扩展 `ATHLETE_*` 前缀集中登记。任务监控通过既有 `business_phase` + `task_type` 筛选区分两侧，不新增筛选参数。

## 技术背景

**语言/版本**: Python 3.11（`/opt/conda/envs/coaching/bin/python3.11`；章程附加约束"Python 环境隔离"，禁止系统 Python）
**主要依赖**: FastAPI（API）、SQLAlchemy 2.x Async（ORM）、Alembic（迁移）、Celery 5.x（异步任务 + Beat）、qcloud-cos（COS SDK）、YOLOv8 + MediaPipe（姿态估计）、Venus/OpenAI LLM（改进建议）；**不引入新依赖**
**存储**: PostgreSQL（主业务表 + 新增 `athletes` / `athlete_video_classifications` 两张表，`diagnosis_reports` 增补 3 列）；COS（原视频 + 预处理分段）；Redis（Celery broker / result backend）
**测试**: pytest + pytest-asyncio + httpx.AsyncClient（路由合约测试）+ pytest-postgresql（迁移测试）
**目标平台**: Linux 服务器，内部运营环境
**项目类型**: 单一后端项目（无前端交付；章程附加约束"前端路径 MUST NOT 创建"）
**性能目标**:
- 扫描阶段：100 条素材 `.mp4` 全量扫描 ≤ 5 min（与 F-008 教练侧相当，瓶颈在 COS list + LLM 兜底）
- 预处理阶段：复用 F-016，并发=3，单条 300 秒视频端到端 ≤ 120 秒（仅限合规标准化 + 分段上传）
- 诊断阶段：复用 `diagnosis` 队列（并发=2），单条诊断 p95 ≤ 60 秒（姿态 + 维度 + LLM，合规 LLM 超时 20s）

**精准度基准**（原则 VIII）: 复用 F-011 / F-013 既有基准——姿态关键点可用率、维度量测一致性、综合评分一致性，本 feature **不新立基准、不引入退化**；发布前以样本集对照基准，相对差值 ≤ 基准波动带（SC-008 约束）。
**约束条件**:
- 分页遵循章程 v1.4.0：`page`/`page_size`，`page_size ∈ [1,100]`，越界 422 `INVALID_PAGE_SIZE`
- 响应统一 `SuccessEnvelope[T]` 成功信封 / `{success:false,error:{code,message,details}}` 错误信封
- 错误码只增不改：新增 `ATHLETE_*` 前缀 MUST 同步 3 张表（`ErrorCode` 枚举 / `ERROR_STATUS_MAP` / `ERROR_DEFAULT_MESSAGE`）+ `contracts/error-codes.md`
- 两张素材表（`coach_video_classifications` 与 `athlete_video_classifications`）DB 物理隔离，禁止合表
- **数据隐私（章程原则 VII）**：运动员原始视频在 COS 上以独立根路径隔离；同意与保留策略沿用专家侧既有政策（两者均内部运营环境，无对外采集通道）；本 feature 不触发第二套数据保留字段
**规模/范围**: 单 feature 新增 ≈ 2 张表 + 2 个服务骨架（scanner + submission 桥接）+ 2 个路由 + 3~5 个 Celery task 注册（实为复用，仅新增两条静态路由绑定）+ 1 个 Alembic 迁移（当前 head 为 `0017_kb_per_category_redesign`，本次使用 `0018_athlete_inference_pipeline`）+ 6 个新 `ErrorCode`

## 章程检查

*门控: 必须在阶段 0 研究前通过. 阶段 1 设计后重新检查.*

**章程合规验证**（对 v2.0.0）：
- ✅ 规范包含量化精准度指标（原则 VIII）——SC-002 95% 扫描分类成功率 / SC-003 批量诊断加速 ≥ 40% / SC-005 ≤ 1 跳反查；姿态估计与诊断评分复用已立基准（F-011/F-013 沿用）
- ✅ 无前端实现任务混入范围——`spec.md` 与本 `plan.md` 仅后端 / 服务 / 路由 / Celery task，零前端（附加约束）
- ✅ 涉及 AI 模型（TechClassifier / pose_estimator / LLM advisor）——全部复用现有模型版本登记，**不新增模型**，原则 VI 合规
- ✅ 涉及用户数据（运动员视频）——COS 独立根路径 + 内部运营环境 + 无对外采集通道 + 保留策略沿用现有政策，原则 VII 合规；**Clarifications Q5 明确本 feature 不提供遗忘权接口**（运维手工处置），已写入 `spec.md` Assumptions 作范围外声明
- ✅ API 接口设计符合原则 IX（统一前缀 `/api/v1/` + 资源化路由 + 分页 `page/page_size` + `SuccessEnvelope`/`AppException` + 错误码集中化 + 合约测试前置 + 下线直接物理删除）
- ✅ 业务流程对齐符合原则 X
  - `spec.md` 已含「业务阶段映射」节（所属阶段 = `INFERENCE`；所属步骤 = `diagnose_athlete` + 新扩展 `scan_athlete_videos` / `preprocess_athlete_video`；DoD 引用 § 2 诊断行；可观测锚点 § 7.1 / § 7.2 / § 7.3）
  - **新扩展步骤 MUST 先扩 business-workflow.md**：**已执行**——在 § 5 追加 5.1/5.2/5.3 小节覆盖 `scan_athlete_videos` 与 `preprocess_athlete_video`；§ 3.1 追加"队列复用"说明；§ 7.4 追加 5 个 `ATHLETE_*` / `STANDARD_NOT_AVAILABLE` 错误码登记（阶段 0 前置完成）
  - 队列拓扑变化：**无新增队列**，仅复用现有 `default` / `preprocessing` / `diagnosis`；business-workflow.md § 3.1 队列复用说明同步
  - 状态机枚举变化：`AnalysisTask.task_type` 新增 2 个值（`athlete_video_classification` / `athlete_video_preprocessing`），同步扩展 `_phase_step_hook` 派生矩阵 + `_PHASE_STEP_TASK_TYPE_MATRIX` + `_PHASE_TASK_TYPES` + `tasks.py::_VALID_BUSINESS_STEPS` 白名单
  - 错误码前缀变化：新增 `ATHLETE_ROOT_UNREADABLE` / `ATHLETE_VIDEO_NOT_PREPROCESSED` / `STANDARD_NOT_AVAILABLE` / `ATHLETE_VIDEO_POSE_UNUSABLE` / `ATHLETE_DIRECTORY_MAP_MISSING` / `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND`，已同步 business-workflow.md § 7.4
  - 诊断评分公式：**无变化**，直接复用 § 5.5（原 § 5.3）公式
  - 单 active / 冲突门控：**无变化**，本 feature 纯只读消费 active 标准
- ✅ 优化活动命中 § 9 三种杠杆：本 feature 不是优化活动，属于"新增能力"；不需要命中杠杆分类
- ✅ 高风险操作引用 § 10 回滚剧本：本 feature 新增的三类任务均为**冷链路 / 只写自己表**，无 KB 激活 / 通道熔断类不可逆操作；回滚策略在下方「复杂度跟踪 / 回滚」中单独声明（low-risk 档位，无需扩 § 10）

**门控结论**：✅ 全部通过，可进入阶段 0。

## 项目结构

### 文档（本功能）

```
specs/020-athlete-inference-pipeline/
├── plan.md              # 本文件（/speckit.plan 输出）
├── spec.md              # /speckit.specify + /speckit.clarify 产物
├── research.md          # 阶段 0 输出（/speckit.plan）
├── data-model.md        # 阶段 1 输出（/speckit.plan）
├── quickstart.md        # 阶段 1 输出（/speckit.plan）
├── contracts/           # 阶段 1 输出（/speckit.plan）
│   ├── error-codes.md                    # 新增 ATHLETE_* + STANDARD_NOT_AVAILABLE 登记
│   ├── athlete_scan.md                   # POST /api/v1/athlete-classifications/scan
│   ├── athlete_scan_status.md            # GET  /api/v1/athlete-classifications/scan/{task_id}
│   ├── athlete_classifications_list.md   # GET  /api/v1/athlete-classifications
│   ├── submit_athlete_preprocessing.md   # POST /api/v1/tasks/athlete-preprocessing
│   ├── submit_athlete_diagnosis.md       # POST /api/v1/tasks/athlete-diagnosis (单 + 批量)
│   └── athlete_reports_list.md           # GET  /api/v1/diagnosis-reports?athlete_id=... (P3)
└── tasks.md             # 阶段 2 输出（/speckit.tasks，本命令不创建）
```

### 源代码（仓库根目录）

采用**选项 1：单一项目**（已删除选项 2/3 占位）：

```
src/
├── api/
│   ├── routers/
│   │   ├── athlete_classifications.py    # 新增：扫描 + 素材清单 CRUD
│   │   └── tasks.py                       # 扩展：新增 POST /tasks/athlete-preprocessing
│   │                                      # + POST /tasks/athlete-diagnosis（单 + 批量）
│   ├── schemas/
│   │   └── athlete_classification.py     # 新增：ScanRequest / AthleteClassificationItem / ...
│   └── errors.py                          # 扩展：新增 5 个 ErrorCode + 映射
├── models/
│   ├── athlete.py                         # 新增：Athlete ORM（与 Coach 对称）
│   └── athlete_video_classification.py    # 新增：AthleteVideoClassification ORM
├── services/
│   ├── cos_athlete_scanner.py             # 新增：运动员侧扫描器（与 cos_classification_scanner 对称）
│   ├── athlete_submission_service.py      # 新增：预处理/诊断提交桥接（复用 preprocessing_service / DiagnosisService）
│   └── diagnosis_service.py               # 扩展：新增 diagnose_athlete_by_classification_id() 入口
├── workers/
│   ├── athlete_scan_task.py               # 新增：scan_athlete_videos Celery task（路由到 default）
│   └── athlete_diagnosis_task.py          # 扩展：增加"按素材 ID 触发"分支
├── db/migrations/versions/
│   └── 0018_athlete_inference_pipeline.py # 新增：2 张新表 + diagnosis_reports 3 列
└── config/
    └── ...                                 # 不引入新 YAML

config/
└── athlete_directory_map.json             # 新增：运动员目录 → 姓名映射（静态 JSON）

tests/
├── contract/
│   ├── test_athlete_scan.py                     # 合约测试：POST /athlete-classifications/scan
│   ├── test_athlete_scan_status.py
│   ├── test_athlete_classifications_list.py
│   ├── test_submit_athlete_preprocessing.py
│   ├── test_submit_athlete_diagnosis.py
│   └── test_athlete_reports_list.py
├── integration/
│   ├── test_athlete_scan_integration.py         # 扫描端到端
│   ├── test_athlete_preprocessing_integration.py
│   ├── test_athlete_diagnosis_end_to_end.py
│   └── test_business_phase_filter_isolation.py  # 任务监控两侧互斥验证（SC-004）
└── unit/
    ├── test_cos_athlete_scanner.py
    ├── test_athlete_submission_service.py
    ├── test_athlete_phase_step_hook.py          # _phase_step_hook 新增 2 条映射单测
    └── test_errors_athlete_codes.py             # 新 ErrorCode 映射单测
```

**结构决策**: 严格沿用现有单一项目布局（`src/` + `tests/` + `config/` + `specs/` 四大顶层目录），**不新建平行目录**。运动员侧与教练侧在 service / router / model / migration 四层独立模块，算法底座（pose / scorer / classifier / preprocessing orchestrator / LLM advisor）层共用，满足章程原则 IV "简洁性与 YAGNI"。

## 复杂度跟踪

本 feature 未引入任何超出章程或规范要求的额外抽象。所有"看似重复"的独立模块（scanner / router / schemas / model / migration）均由章程原则 I + 附加约束"两张视频分类表并存（禁止合并）"直接要求，不构成违规。

**回滚策略（低风险档，无需扩 business-workflow.md § 10）**：

| 风险场景 | 回滚动作 | 影响面 |
|---------|---------|--------|
| 扫描任务误入脏数据 | 停 `default` worker + `DELETE FROM athlete_video_classifications WHERE created_at >= ...` + `DELETE FROM athletes WHERE created_via='athlete_scan' AND created_at >= ...` | 仅运动员侧素材清单，不触教练侧 / KB / standards |
| 预处理产物损坏 | `UPDATE athlete_video_classifications SET preprocessed=false, preprocessing_job_id=null WHERE ...` + COS 分段对象清理（复用 `cleanup_intermediate_artifacts`） | 仅影响"该素材下次诊断"，老诊断报告不受影响 |
| 诊断报告异常 | 直接 `DELETE FROM diagnosis_reports WHERE cos_object_key IN (...)`；KB / standards 未被修改 | 仅诊断结论侧 |
| 迁移失败 | `alembic downgrade 0017_kb_per_category_redesign` 回退 2 张表 + 3 列；无 schema 破坏性变更（只增不减） | 可逆 |

所有回滚路径均为**局部可逆**，不涉及 KB 版本激活 / 通道熔断等全局状态切换，不需要新扩章程级回滚剧本。
