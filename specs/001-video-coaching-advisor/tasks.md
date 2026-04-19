# 任务: 视频教学分析与专业指导建议

**输入**: 来自 `/specs/001-video-coaching-advisor/` 的设计文档
**前置条件**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/api.md ✅, quickstart.md ✅

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1, US2, US3）
- 描述中包含确切的文件路径

---

## 阶段 1: 设置（共享基础设施）

**目的**: Python 项目初始化、依赖管理、目录骨架搭建

- [x] T001 按 plan.md 项目结构创建目录骨架（src/api/routers/, src/api/schemas/, src/models/, src/services/, src/workers/, src/db/migrations/, tests/unit/, tests/integration/, tests/contract/, tests/benchmarks/, docs/benchmarks/）
- [x] T002 创建 pyproject.toml 并声明全部依赖：fastapi, uvicorn, celery[redis], mediapipe, opencv-python-headless, ffmpeg-python, sqlalchemy[asyncio], alembic, psycopg2-binary, pydantic-settings, cos-python-sdk-v5, pytest, pytest-asyncio, httpx
- [x] T003 [P] 创建 .env.example，列出所有必要环境变量：DATABASE_URL, REDIS_URL, COS_SECRET_ID, COS_SECRET_KEY, COS_REGION, COS_BUCKET, TMP_DIR（值均为占位符，不含真实凭证）
- [x] T004 [P] 创建 src/config.py：用 pydantic BaseSettings 从环境变量读取全部配置（含 COS 四项凭证），不硬编码任何密钥；提供 get_settings() 单例
- [x] T005 [P] 在 pyproject.toml 的 [tool.pytest.ini_options] 中配置 pytest（asyncio_mode=auto，testpaths，markers: unit/integration/contract/benchmark）

---

## 阶段 2: 基础（阻塞性前置条件）

**目的**: 数据库迁移框架、Celery、FastAPI 骨架——所有用户故事开始前必须完成

**⚠️ 关键**: 在此阶段完成之前，无法开始任何用户故事工作

- [x] T006 初始化 Alembic（`alembic init src/db/migrations`），在 src/db/session.py 中配置 SQLAlchemy 异步引擎和 AsyncSession 工厂，连接串从 config.py 读取
- [x] T007 创建初始 Alembic 迁移脚本（`src/db/migrations/versions/0001_initial_schema.py`），建立全部 6 张表：analysis_tasks, expert_tech_points, tech_knowledge_bases, athlete_motion_analyses, deviation_reports, coaching_advice；包含 data-model.md 中全部字段、唯一约束、索引（idx_expert_point_action_type, idx_task_status, idx_task_deleted, idx_deviation_action_dim, idx_advice_task）
- [x] T008 [P] 配置 src/workers/celery_app.py：Celery 应用实例、broker/backend 指向 Redis（从 config.py 读取）、任务超时 8 分钟（soft_time_limit=480）、失败后重试 2 次（max_retries=2, default_retry_delay=30）
- [x] T009 [P] 实现 src/api/main.py：FastAPI 应用入口，注册 tasks 和 knowledge_base 两组路由，全局异常处理器（返回统一结构 `{error:{code,message,details}}`），结构化 JSON 日志初始化（含 request_id middleware）
- [x] T010 [P] 实现 src/api/routers/tasks.py 路由骨架（POST /tasks/expert-video, POST /tasks/athlete-video, GET /tasks/{task_id}, GET /tasks/{task_id}/result, DELETE /tasks/{task_id}），全部返回 HTTP 501 占位响应
- [x] T011 [P] 实现 src/api/routers/knowledge_base.py 路由骨架（GET /knowledge-base/versions, GET /knowledge-base/{version}, POST /knowledge-base/{version}/approve），全部返回 HTTP 501 占位响应
- [x] T012 [P] 创建 src/api/schemas/task.py：ExpertVideoRequest, AthleteVideoRequest, TaskStatusResponse, TaskResultAthleteResponse, TaskResultExpertResponse 等全部 Pydantic v2 模型，字段与 contracts/api.md 响应结构严格一致
- [x] T013 [P] 创建 src/api/schemas/knowledge_base.py：KnowledgeBaseVersionItem, KnowledgeBaseVersionsResponse, KnowledgeBaseDetailResponse, ApproveRequest, ApproveResponse 等 Pydantic 模型

**检查点**: 可运行 `alembic upgrade head` 建表成功；FastAPI 应用启动；Celery worker 注册成功；所有路由骨架返回 501

---

## 阶段 3: 用户故事 1 — 从教练视频提取技术知识库（优先级: P1）🎯 MVP

**目标**: 管理员调用 POST /tasks/expert-video 提交 COS 中的教练视频，系统异步下载、分析、提取技术要点，构建版本化知识库草稿，专家通过 POST /knowledge-base/{version}/approve 审核激活

**独立测试**: 提交已知内容正手拉球教练视频（cos_object_key），轮询至 success，GET /tasks/{id}/result 验证维度覆盖率 ≥90%（身体姿态/挥拍轨迹/击球时机/重心转移）；再调用 approve 接口激活知识库；无需运动员视频即可完整测试

### 用户故事 1 的实施

- [x] T014 [P] [US1] 创建 src/models/analysis_task.py：AnalysisTask SQLAlchemy ORM 模型（所有字段、task_type/status 枚举、deleted_at 软删除）
- [x] T015 [P] [US1] 创建 src/models/expert_tech_point.py：ExpertTechPoint ORM 模型（不可变记录；唯一约束 (knowledge_base_version, action_type, dimension)；验证 param_min ≤ param_ideal ≤ param_max）
- [x] T016 [P] [US1] 创建 src/models/tech_knowledge_base.py：TechKnowledgeBase ORM 模型（version 为 PK；status 枚举 draft/active/archived；approved_by/approved_at）
- [x] T017 [US1] 实现 src/services/cos_client.py：COS 客户端封装（object_exists(key: str) → bool；download_to_temp(key: str) → Path，下载到 config.TMP_DIR；cleanup_temp_file(path: Path)；所有凭证来自 config.py；COS SDK 不存在时抛出 CosObjectNotFoundError，下载失败时抛出 CosDownloadError）
- [x] T018 [US1] 实现 src/services/video_validator.py：视频质量门控（用 cv2.VideoCapture 读取 fps、分辨率；fps < 15 → VideoQualityRejected("fps_too_low")；分辨率 < 854×480 → VideoQualityRejected("resolution_too_low")；无法打开 → VideoQualityRejected("unreadable")；返回 VideoMeta(fps, resolution, duration_seconds)）
- [x] T019 [US1] 实现 src/services/pose_estimator.py：MediaPipe Pose 封装（逐帧推理 33 关键点；过滤 visibility < 0.5 的关键点；返回 List[FramePoseResult]，每帧含 keypoints dict 和 frame_confidence；DEBUG 级日志输出原始坐标和置信度）
- [x] T020 [US1] 实现 src/services/action_segmenter.py：基于腕关节（landmark 15/16）速度峰值检测动作片段（峰值检测窗口滑动；以击球点为中心前后各 0.5 秒划定片段边界；返回 List[ActionSegment(start_ms, end_ms, wrist_peak_ms)]）
- [x] T021 [US1] 实现 src/services/action_classifier.py：规则分类器 v1（基于击球前挥拍方向、肘关节角度变化率判断 forehand_topspin / backhand_push / unknown；输入 ActionSegment 对应的姿态序列；v1 只支持 2 类，unknown 时记录 action_type="unknown"）
- [x] T022 [US1] 实现 src/services/tech_extractor.py：专家技术要点提取（输入：动作片段姿态序列 + 动作类型；计算 4 个维度的标准参数范围：elbow_angle(°), swing_trajectory(ratio), contact_timing(ms), weight_transfer(ratio)；每个维度输出 param_min/param_max/param_ideal/unit/extraction_confidence；extraction_confidence < 0.7 的维度不录入知识库）
- [x] T023 [US1] 实现 src/services/knowledge_base_svc.py：知识库版本管理（create_draft_version(action_types) → version_str；add_tech_points(version, points)；approve_version(version, approved_by, notes) 将其设为 active 并将原 active 归档；get_active_version() → TechKnowledgeBase | None；enforce 单 active 约束）
- [x] T024 [US1] 实现 src/workers/expert_video_task.py：Celery 任务 process_expert_video（流程：cos_client.object_exists → 失败更新 status=failed error=COS_OBJECT_NOT_FOUND；download_to_temp；validate_video → 失败更新 status=rejected rejection_reason；pose_estimate；segment；classify；extract_tech_points；save ExpertTechPoints；create draft KB version；cleanup_temp_file；更新 status=success completed_at；任意异常更新 status=failed error_message 并清理临时文件）
- [x] T025 [US1] 实现 POST /tasks/expert-video 端点完整逻辑（src/api/routers/tasks.py）：解析 ExpertVideoRequest；cos_client.object_exists 检查（不存在返回 404 COS_OBJECT_NOT_FOUND）；创建 AnalysisTask(task_type=expert_video, status=pending)；触发 process_expert_video.delay(task_id, cos_object_key)；返回 202 含 task_id/status/estimated_completion_seconds=300
- [x] T026 [US1] 实现 GET /tasks/{task_id} 端点完整逻辑：查询 AnalysisTask；deleted_at IS NOT NULL 或不存在 → 404 TASK_NOT_FOUND；返回 task_id/task_type/status/created_at/started_at/completed_at/video_duration_seconds/video_fps/video_resolution
- [x] T027 [US1] 实现 GET /tasks/{task_id}/result 端点 expert_video 分支：查询 ExpertTechPoint by source_video_id；构建 extracted_points 列表；返回 knowledge_base_version_draft/extracted_points_count/extracted_points/pending_approval
- [x] T028 [US1] 实现 GET /knowledge-base/versions 和 GET /knowledge-base/{version} 端点（src/api/routers/knowledge_base.py）：分别查询全部版本列表和单版本详情，含 ExpertTechPoint 列表
- [x] T029 [US1] 实现 POST /knowledge-base/{version}/approve 端点：调用 knowledge_base_svc.approve_version；返回新 active 版本信息及 previous_active_version
- [x] T030 [US1] 实现 DELETE /tasks/{task_id} 端点：设置 deleted_at=now()（软删除）；返回 200 含 deleted_at 和"24 小时内物理清除"说明

**检查点**: US1 完整可用 — quickstart.md curl 示例端到端可跑通：提交教练视频 → 轮询 success → 获取提取结果 → 审核激活知识库

---

## 阶段 4: 用户故事 2 — 分析运动员视频并定位技术偏差（优先级: P2）

**目标**: 运动员调用 POST /tasks/athlete-video 上传打球视频，系统与激活知识库定量比对，输出结构化偏差报告（偏差值、方向、置信度、稳定性标注）

**独立测试**: 给定预标注偏差的运动员视频 + 已激活知识库，GET /tasks/{id}/result 中 deviation_report 与人工标注基准对比，一致率 ≥85%（偏差类型和方向均正确）；建议生成模块可尚未实现

### 用户故事 2 的实施

- [x] T031 [P] [US2] 创建 src/models/athlete_motion_analysis.py：AthleteMotionAnalysis ORM 模型（task_id FK、action_type 枚举含 unknown、segment_start_ms/segment_end_ms、measured_params JSONB、overall_confidence、is_low_confidence、knowledge_base_version FK）
- [x] T032 [P] [US2] 创建 src/models/deviation_report.py：DeviationReport ORM 模型（analysis_id FK、expert_point_id FK、dimension、measured_value、ideal_value、deviation_value、deviation_direction 枚举 above/below/none、confidence、is_low_confidence、is_stable_deviation NULLABLE、impact_score NULLABLE）
- [x] T033 [US2] 实现 src/services/deviation_analyzer.py：偏差计算服务（输入：AthleteMotionAnalysis measured_params + ExpertTechPoint 标准；计算 deviation_value = measured - ideal；deviation_direction：measured > param_max → above，< param_min → below，否则 none；impact_score = abs(deviation_value) / (param_max - param_min)，归一化到 [0,1]；overall_confidence < 0.7 → is_low_confidence=true）
- [x] T034 [US2] 在 src/services/deviation_analyzer.py 中添加稳定性聚合计算 compute_stability(task_ids: List[UUID], action_type, dimension)：查询同 athlete 历史分析记录；样本 ≥3 且偏差（deviation_direction ≠ none）出现率 ≥70% → is_stable_deviation=true；样本 < 3 → NULL；否则 false
- [x] T035 [US2] 实现 src/workers/athlete_video_task.py：Celery 任务 process_athlete_video（流程：保存上传文件到临时目录；更新 status=processing；validate_video → 失败 status=rejected；get_active_version → None 则 status=failed error=KNOWLEDGE_BASE_NOT_READY；pose_estimate；segment；classify；对每个有效片段（unknown 类型记录但跳过比对）：save AthleteMotionAnalysis；调用 deviation_analyzer 计算并保存 DeviationReport；计算稳定性并更新 is_stable_deviation；cleanup；status=success）
- [x] T036 [US2] 实现 POST /tasks/athlete-video 端点完整逻辑（src/api/routers/tasks.py）：接收 multipart（video 文件、knowledge_base_version 可选、target_person_index 可选）；校验参数（无文件 → 400）；创建 AnalysisTask(task_type=athlete_video)；触发 process_athlete_video.delay(task_id, tmp_path, kb_version, target_person_index)；返回 202 含 task_id/status/knowledge_base_version/estimated_completion_seconds=300
- [x] T037 [US2] 实现 GET /tasks/{task_id}/result 端点 athlete_video 分支：查询 AthleteMotionAnalysis + DeviationReport；构建 motion_analyses 列表（含 deviation_report 子数组）；计算 summary（total_actions_detected、actions_analyzed、actions_low_confidence、total_deviations、stable_deviations、top_advice_dimension）；CoachingAdvice 字段此阶段返回空数组占位

**检查点**: US2 可独立测试 — 上传运动员视频后 GET /result 可获取 deviation_report，偏差维度、方向、置信度字段完整；US1 功能不受影响

---

## 阶段 5: 用户故事 3 — 生成结构化专业指导建议（优先级: P3）

**目标**: 基于偏差报告为运动员生成按影响程度排序的可操作专业指导建议，每条建议关联具体偏差，高/低置信度分别标注

**独立测试**: 给定一份预生成的 DeviationReport（mock 输入），GET /tasks/{id}/result 中 coaching_advice 包含针对每项偏差的独立建议（偏差描述+改进目标+改进方法），按 impact_score DESC 排序，低置信度建议有 reliability_note

### 用户故事 3 的实施

- [x] T038 [P] [US3] 创建 src/models/coaching_advice.py：CoachingAdvice ORM 模型（deviation_id FK、task_id FK、deviation_description、improvement_target、improvement_method、impact_score、reliability_level 枚举 high/low、reliability_note NULLABLE、created_at）
- [x] T039 [US3] 实现 src/services/advice_generator.py：指导建议生成服务（输入：DeviationReport 列表 + 对应 ExpertTechPoint；对每条偏差（deviation_direction ≠ none）生成 CoachingAdvice：deviation_description 含维度名称和偏差量（如"正手拉球肘部角度偏大 32.5°"）；improvement_target 引用 ExpertTechPoint.param_min/ideal/param_max；improvement_method 为可操作的文字训练建议；reliability_level：confidence ≥ 0.7 → high；< 0.7 → low 并填写 reliability_note；impact_score 继承 DeviationReport.impact_score；按 impact_score DESC 排序输出）
- [x] T040 [US3] 在 src/workers/athlete_video_task.py 的 process_athlete_video 任务中，偏差计算完成后调用 advice_generator.generate(deviation_reports) 并保存 CoachingAdvice 记录
- [x] T041 [US3] 更新 GET /tasks/{task_id}/result 端点 athlete_video 分支（src/api/routers/tasks.py）：填充 coaching_advice 数组（替换阶段 4 中的空数组占位），含 advice_id/dimension/deviation_description/improvement_target/improvement_method/impact_score/reliability_level/reliability_note；summary.top_advice_dimension 指向 impact_score 最高的维度

**检查点**: 完整流程可通 — 上传运动员视频，GET /result 返回含 coaching_advice 的完整响应，建议按 impact_score 降序排列，低置信度建议有说明

---

## 阶段 6: 完善与横切关注点

**目的**: 数据保留、精准度基准、可观测性、安全加固

- [x] T042 实现定时任务（src/workers/celery_app.py 中配置 Celery Beat 调度）：每日扫描 analysis_tasks，物理删除 deleted_at IS NOT NULL 或 completed_at < NOW() - interval '12 months' 的记录及关联数据（CASCADE）；日志记录清理数量
- [x] T043 [P] 在 src/models/analysis_task.py 中为 video_storage_uri 字段添加应用层加密（使用 SQLAlchemy TypeDecorator 封装 AES-256-GCM 加解密，密钥通过 config.py 环境变量注入）
- [x] T044 [P] 创建 docs/benchmarks/README.md：说明基准数据集格式、来源、版本管理规范（Git LFS）
- [x] T045 [P] 创建 docs/benchmarks/expert_annotation_v1.json：人工标注的技术维度覆盖率基准数据集（格式：视频片段 ID → 期望维度列表），用于 SC-001（维度覆盖率 ≥90%）验证
- [x] T046 [P] 创建 docs/benchmarks/deviation_annotation_v1.json：人工标注的偏差基准数据集（格式：视频片段 ID → 期望偏差列表含 dimension/direction），用于 SC-002（一致率 ≥85%）验证
- [x] T047 创建 tests/benchmarks/test_accuracy_benchmarks.py：精准度基准测试（加载 expert_annotation_v1.json 运行 SC-001；加载 deviation_annotation_v1.json 运行 SC-002；测试失败时阻止合并）
- [x] T048 [P] 创建 tests/unit/test_cos_client.py：COS 客户端单元测试（mock cos-python-sdk-v5；测试 object_exists 返回 True/False；测试 download_to_temp 成功路径；测试 CosObjectNotFoundError / CosDownloadError 异常路径）
- [x] T049 [P] 创建 tests/unit/test_video_validator.py：视频质量门控单元测试（mock cv2.VideoCapture；测试 fps 不足/分辨率不足/无法读取三种拒绝场景；测试正常视频通过）
- [x] T050 [P] 创建 tests/unit/test_deviation_analyzer.py：偏差计算单元测试（已知输入验证 deviation_value/direction/impact_score 计算正确；验证 confidence < 0.7 时 is_low_confidence=true；验证稳定性聚合逻辑在样本不足时返回 NULL）
- [x] T051 [P] 创建 tests/unit/test_advice_generator.py：建议生成单元测试（给定 mock DeviationReport 列表，验证每条偏差生成一条建议；验证 reliability_level 高/低分支；验证按 impact_score 降序排列）
- [x] T052 创建 tests/contract/test_api_contracts.py：API 接口契约测试（用 httpx.AsyncClient 对运行中的 FastAPI 测试所有 8 个端点的请求/响应结构；验证错误码格式；验证 202/200/404/422 状态码）
- [x] T053 创建 tests/integration/test_expert_pipeline.py：专家视频端到端集成测试（mock COS SDK；使用测试夹具视频文件；验证 COS 下载 → 质量门控 → 姿态估计 → 提取 → 知识库草稿创建 → 审核激活全链路）
- [x] T054 创建 tests/integration/test_athlete_pipeline.py：运动员视频端到端集成测试（依赖已激活知识库夹具；上传测试视频；验证偏差分析 → 建议生成全链路，含 low_confidence 片段标注）
- [x] T055 创建 tests/integration/test_data_retention.py：数据保留与删除集成测试（验证 DELETE /tasks/{id} 软删除后立即返回 404；验证定时清理任务对 deleted_at 记录物理删除）
- [ ] T056 [P] 按 quickstart.md 验证全部 curl 示例可端到端跑通（含 COS 环境变量设置步骤）

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **阶段 1（设置）**: 无依赖，可立即开始
- **阶段 2（基础）**: 依赖阶段 1 完成 — 阻塞所有用户故事
- **阶段 3（US1 P1）**: 依赖阶段 2 完成 — MVP 最优先交付
- **阶段 4（US2 P2）**: 依赖阶段 2 完成，集成时依赖阶段 3 激活的知识库
- **阶段 5（US3 P3）**: 依赖阶段 4 完成（建议生成依赖偏差报告）
- **阶段 6（完善）**: 依赖阶段 3~5 全部完成

### 用户故事内部顺序

- 模型（T014~T016, T031~T032, T038）→ 服务（T017~T023, T033~T034, T039）→ Worker 任务（T024, T035, T040）→ API 端点（T025~T030, T036~T037, T041）
- COS 客户端（T017）必须先于 Worker 任务（T024）完成
- 知识库服务（T023）必须先于 approve 端点（T029）完成
- 偏差分析（T033/T034）必须先于 athlete worker（T035）完成
- 指导建议生成（T039）必须先于 worker 集成（T040）完成

### 并行机会

- **阶段 1**: T003, T004, T005 可与 T002 并行
- **阶段 2**: T008~T013 均可与 T006~T007 并行执行（T006 完成后 T007 才开始）
- **阶段 3 模型层**: T014, T015, T016 可并行
- **阶段 4 模型层**: T031, T032 可并行，且可与阶段 3 服务层并行
- **阶段 6**: T042~T056 中标 [P] 的任务均可并行，基准数据创建（T044~T047）可与单元测试（T048~T051）并行

---

## 并行执行示例

```bash
# 阶段 3 模型层并行（三个文件互不依赖）:
任务 T014: 创建 src/models/analysis_task.py
任务 T015: 创建 src/models/expert_tech_point.py
任务 T016: 创建 src/models/tech_knowledge_base.py

# 阶段 3 服务层大部分可并行启动（T017 完成后才能测试 T024）:
任务 T018: 实现 src/services/video_validator.py
任务 T019: 实现 src/services/pose_estimator.py
任务 T020: 实现 src/services/action_segmenter.py
任务 T021: 实现 src/services/action_classifier.py

# 阶段 6 单元测试全部并行:
任务 T048: tests/unit/test_cos_client.py
任务 T049: tests/unit/test_video_validator.py
任务 T050: tests/unit/test_deviation_analyzer.py
任务 T051: tests/unit/test_advice_generator.py
```

---

## 实施策略

### 仅 MVP（仅用户故事 1）

1. 完成阶段 1：设置
2. 完成阶段 2：基础（关键阻塞步骤）
3. 完成阶段 3：用户故事 1（COS 教练视频 → 知识库提取 → 专家审核激活）
4. **停止并验证**: quickstart.md curl 示例端到端可跑通；SC-001 维度覆盖率 ≥90%
5. 知识库激活后可演示 MVP

### 增量交付

1. 阶段 1+2 → 基础就绪
2. 阶段 3（US1）→ 独立测试 → **MVP 可演示**
3. 阶段 4（US2）→ 独立测试 → 偏差诊断可演示（SC-002 基准验证）
4. 阶段 5（US3）→ 独立测试 → 完整建议生成可演示（SC-003 专家评审）
5. 阶段 6 → 数据保留 + 精准度基准 + 安全加固 → 生产就绪

### 并行团队策略

基础完成后：
- **开发者 A**: US1（COS 下载 + 姿态估计 + 技术提取 + 知识库管理）
- **开发者 B**: US2（偏差分析 + 稳定性计算 + 运动员 Worker）
- **开发者 C**: 基准数据集 + 精准度测试（docs/benchmarks/ + tests/benchmarks/）

---

## 注意事项

- [P] 任务 = 不同文件，无依赖关系，可并行分配
- [Story] 标签将任务映射到用户故事，支持独立交付追溯
- COS 凭证绝不硬编码，测试中用 mock 替代真实 SDK 调用
- 每个用户故事检查点处应可独立验证，不需要后续故事代码
- 精准度基准（T044~T047）须在首次实现完成后立即建立，不可推迟至上线前
- video_storage_uri 加密（T043）应在任何真实视频路径写入数据库前完成
