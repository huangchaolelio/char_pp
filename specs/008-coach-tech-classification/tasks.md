# 任务: 教练视频技术分类数据库 (Feature 008)

**输入**: 来自 `/specs/008-coach-tech-classification/` 的设计文档
**前置条件**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/api.md ✅

**组织结构**: 任务按用户故事分组，每个故事可独立实施和测试。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1, US2, US3, US4）

---

## 阶段 1: 设置（共享基础设施）

**目的**: 配置文件、数据库迁移，为所有用户故事解锁

- [X] T001 创建 `config/coach_directory_map.json`（20 个教练课程系列目录名 → 教练姓名映射，参见 research.md Decision 2）
- [X] T002 创建 `config/tech_classification_rules.json`（21 类技术 → 关键词列表映射，参见 research.md Decision 1）
- [X] T003 创建数据库迁移 `src/db/migrations/versions/0009_coach_video_classifications.py`（新建 `coach_video_classifications` 表，参见 data-model.md；down_revision="0008"）
- [X] T004 执行数据库迁移（`alembic upgrade head`，验证表和索引创建成功）

---

## 阶段 2: 基础（阻塞性前置条件）

**目的**: ORM 模型和核心分类器，所有用户故事依赖

**⚠️ 关键**: 阶段 1 完成后才能开始

- [X] T005 创建 ORM 模型 `src/models/coach_video_classification.py`（继承 `src/db/session.py` 的 Base，字段参见 data-model.md；包含 `__tablename__ = "coach_video_classifications"`，所有字段使用 `Mapped[]` 类型注解）
- [X] T006 [P] 创建 Pydantic schemas `src/api/schemas/classification.py`（包含：`ScanRequest`、`ScanStatusResponse`、`ClassificationItem`、`ClassificationListResponse`、`ClassificationSummaryResponse`、`ClassificationPatchRequest`、`ClassificationPatchResponse`，字段参见 contracts/api.md）
- [X] T007 创建分类器单元测试 `tests/unit/test_tech_classifier.py`（覆盖：关键词精确命中→正确 tech_category、多关键词→tech_tags 填充、无关键词→unclassified、LLM mock 兜底返回有效类别；**在 T008 之前创建，确保测试先失败**）
- [X] T008 创建技术分类器服务 `src/services/tech_classifier.py`（`TechClassifier` 类：加载 `config/tech_classification_rules.json`；`classify(filename, course_series)` 方法按顺序扫描规则返回 `(tech_category, tech_tags, source, confidence)`；规则未命中时调用 `LlmClient.from_settings().chat()` 使用 research.md 的 Prompt 模板；confidence < 0.5 降级为 `unclassified`）

---

## 阶段 3: 用户故事 1 — 扫描 COS 并生成技术分类（P1）

**故事目标**: 管理员触发全量扫描，系统遍历 COS 所有教练视频，识别技术类别，写入数据库。

**独立测试标准**: 调用 `POST /api/v1/classifications/scan` 后，查询 `GET /api/v1/classifications` 可看到分类记录，cos_object_key 唯一，unclassified 率 ≤ 5%。

- [X] T009 [US1] 创建 COS 扫描器 `src/services/cos_classification_scanner.py`（`CosClassificationScanner` 类：`__init__` 加载 `config/coach_directory_map.json`；`scan_full(session)` 方法：分页遍历 COS（每页 1000），对每个 `.mp4` 文件调用 `TechClassifier.classify()`，upsert by `cos_object_key`，返回 `{scanned, inserted, updated, skipped, errors}` 统计；`scan_incremental(session)` 方法：先查 DB 已有 key 集合，只处理新文件；结构化日志每 100 条打一次进度）
- [X] T010 [US1] 创建 Celery task `src/workers/classification_task.py`（使用 `@shared_task(bind=True, name="src.workers.classification_task.scan_cos_videos", max_retries=2, default_retry_delay=30, acks_late=True)`；接收 `task_id: str, scan_mode: str`；更新 DB 中扫描任务状态为 running→success/failed；调用 `CosClassificationScanner` 执行扫描；日志包含 inserted/skipped/errors 计数；在 `src/workers/celery_app.py` 的 `include` 列表中注册）
- [X] T011 [US1] 更新 `src/workers/celery_app.py`，在 `include` 列表添加 `"src.workers.classification_task"`
- [X] T012 [US1] 创建分类路由 `src/api/routers/classifications.py`（实现 `POST /api/v1/classifications/scan` 端点：接收 `ScanRequest`，创建异步 Celery task，返回 202 + task_id；参见 contracts/api.md）
- [X] T013 [US1] 在 `src/api/main.py` 中注册 classifications 路由（`from src.api.routers import classifications`；`app.include_router(classifications.router, prefix="/api/v1")`）
- [X] T014 [US1] 创建扫描进度查询端点（在 `src/api/routers/classifications.py` 中添加 `GET /api/v1/classifications/scan/{task_id}`，从 Celery result backend 查询任务状态，返回 `ScanStatusResponse`；参见 contracts/api.md）

---

## 阶段 4: 用户故事 2 — 查询教练技术分类列表（P2）

**故事目标**: 研究人员查询指定教练教授了哪些技术及各技术的视频数量。

**独立测试标准**: `GET /api/v1/classifications?coach_name=孙浩泓` 返回正确的技术列表和视频数量，不存在的教练返回空列表。

**前置条件**: 阶段 3 完成（有分类数据）

- [X] T015 [US2] 在 `src/api/routers/classifications.py` 添加 `GET /api/v1/classifications` 端点（支持 `coach_name`、`tech_category`、`kb_extracted`、`classification_source`、`limit`、`offset` 参数；DB 查询用 SQLAlchemy `select` + 条件过滤；返回 `ClassificationListResponse`；参见 contracts/api.md）
- [X] T016 [US2] 在 `src/api/routers/classifications.py` 添加 `GET /api/v1/classifications/summary` 端点（按 `coach_name` 分组统计各 `tech_category` 数量和 `kb_extracted` 数量；支持可选 `coach_name` 参数过滤；返回 `ClassificationSummaryResponse`；参见 contracts/api.md）

---

## 阶段 5: 用户故事 3 — 按技术类别获取 COS 路径（P2）

**故事目标**: 下游任务按技术类别批量获取待处理视频的 COS 路径，支持 `kb_extracted=false` 过滤。

**独立测试标准**: `GET /api/v1/classifications?tech_category=forehand_topspin&kb_extracted=false` 返回正确的 COS 路径列表，每条记录含 `cos_object_key` 和 `coach_name`。

**前置条件**: 阶段 4 完成（GET 列表端点已实现，`kb_extracted` 过滤复用已有逻辑）

- [X] T017 [US3] 验证 `GET /api/v1/classifications` 端点的 `kb_extracted` 过滤逻辑（在 T015 的 DB 查询中确认 `WHERE kb_extracted = false` 条件正确生成，补充集成测试用例：查询 `kb_extracted=false` 只返回未提取记录）
- [X] T018 [US3] 创建集成测试 `tests/integration/test_classification_scan.py`（测试场景：触发扫描→等待完成→按技术类别查询→验证返回数据结构；使用 httpx 调用实际 API；覆盖 `unclassified` 记录存在场景和 `kb_extracted=false` 过滤场景）

---

## 阶段 6: 用户故事 4 — 增量更新与人工修正（P3）

**故事目标**: 新增视频时只处理新文件；人工可通过 PATCH 接口修正分类错误。

**独立测试标准**: 增量扫描只新增新记录；`PATCH /api/v1/classifications/{id}` 成功将 source 改为 `manual`。

**前置条件**: 阶段 3 完成（扫描基础已实现）

- [X] T019 [US4] 验证 `scan_incremental` 方法逻辑（`CosClassificationScanner.scan_incremental` 中确认：先查已有 `cos_object_key` 集合，COS 遍历时跳过已存在 key，只对新文件调用 `classify()` 并 insert；在 `POST /api/v1/classifications/scan` 中 `scan_mode=incremental` 路由到 `scan_incremental`）
- [X] T020 [US4] 在 `src/api/routers/classifications.py` 添加 `PATCH /api/v1/classifications/{id}` 端点（接收 `ClassificationPatchRequest`，更新 `tech_category`、`tech_tags`，强制设置 `classification_source="manual"`、`confidence=1.0`、`updated_at=NOW()`；校验 `tech_category` 为有效枚举值，无效时返回 400；返回 `ClassificationPatchResponse`；参见 contracts/api.md）

---

## 阶段 7: 收尾与验证

**目的**: 准确率验证、日志完善、文档

- [X] T021 运行单元测试验证分类器（`pytest tests/unit/test_tech_classifier.py -v`，确认全部通过）
- [X] T022 触发全量扫描，验证 SC-001（unclassified 率 ≤ 5%）和 SC-003（耗时 ≤ 5 分钟）
- [X] T023 人工抽检 20 条 `classification_source=rule` 记录和 5 条 `classification_source=llm` 记录，验证 SC-002（rule ≥ 95%，llm ≥ 85%）
- [X] T024 验证 SC-005（`GET /api/v1/classifications` 响应时间 ≤ 500ms，可通过 `time curl` 快速验证）
- [X] T025 [P] 在 `.specify/memory/` 下更新项目记忆，记录 Feature-008 的关键配置文件路径和技术类别 ID 列表

---

## 依赖关系

```
T001, T002          → T003 → T004
                              ↓
T004           → T005 → T006（并行），T007 → T008
                              ↓
T005+T006+T008 → T009 → T010 → T011 → T012 → T013 → T014   [US1 完成]
                                                  ↓
T014           → T015 → T016                              [US2 完成]
                              ↓
T015           → T017 → T018                              [US3 完成]
                              ↓
T009+T014      → T019 → T020                              [US4 完成]
                              ↓
T021, T022, T023, T024, T025                              [收尾]
```

## 并行执行机会

| 并行组 | 任务 | 说明 |
|--------|------|------|
| Setup 并行 | T001, T002 | 两个独立配置文件，无依赖 |
| 基础并行 | T006 与 T005 | schemas 和 ORM 模型无互相依赖 |
| US1 内部并行 | T010, T011（T009 完成后） | Celery task 注册和 celery_app 更新无冲突 |
| 收尾并行 | T021, T022, T023, T024, T025 | 验证任务互相独立 |

## 实现策略

**MVP 范围（阶段 1-3）**: 全量扫描 + 分类 + 入库，可通过 API 触发并查询结果。完成 P1 用户故事即可交付最小可用价值。

**增量交付顺序**:
1. **阶段 1-2**: 基础设施（迁移 + ORM + 分类器）— 本地可验证
2. **阶段 3**: 扫描 + Celery task + API 触发 — MVP 可演示
3. **阶段 4-5**: 查询/统计接口 — 支撑后续知识库提取规划
4. **阶段 6**: 增量扫描 + 人工修正 — 运营维护能力
5. **阶段 7**: 收尾验证 — 质量确认

**总任务数**: 25 个
**各阶段任务数**:
- 阶段 1（Setup）: 4 个
- 阶段 2（基础）: 4 个
- 阶段 3（US1 P1）: 6 个
- 阶段 4（US2 P2）: 2 个
- 阶段 5（US3 P2）: 2 个
- 阶段 6（US4 P3）: 2 个
- 阶段 7（收尾）: 5 个
