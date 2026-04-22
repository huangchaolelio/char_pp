# 实施计划: COS 教学视频分类体系

**分支**: `004-video-classification` | **日期**: 2026-04-20 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/004-video-classification/spec.md` 的功能规范

## 摘要

为 COS 中 120 个孙浩泓教学视频建立持久化三层分类体系（教练 → 技术大类 → 技术细分），支持按分类批量提交知识库提取任务，并将提交任务时的 `action_type_hint` 统一改为从数据库分类表读取，废弃原来的 `infer_action_type_hint()` 调用路径。

核心交付物：
1. `src/config/video_classification.yaml` — 分类树配置（关键词规则 + ActionType 映射）
2. `src/models/video_classification.py` — `VideoClassification` ORM 模型
3. `src/db/migrations/versions/0005_video_classifications.py` — 建表迁移
4. `src/services/video_classifier.py` — 分类服务（关键词匹配 + 置信度）
5. `src/api/routers/videos.py` — 4 个新 API 端点
6. 修改 `src/api/routers/tasks.py` — 废弃 `infer_action_type_hint()`

## 技术背景

**语言/版本**: Python 3.11+
**主要依赖**: FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, PyYAML（已在项目中使用）
**存储**: PostgreSQL — 新增 `video_classifications` 表
**测试**: pytest + pytest-asyncio（现有测试框架）
**目标平台**: Linux 服务器（与现有服务共部署）
**项目类型**: Web 服务（REST API）
**性能目标**: 分类目录 API 响应 < 1 秒（120 条记录，无模型推理）
**约束条件**: 按需触发（无定时任务）；分类树变更后全量重分类；`manually_overridden=true` 的记录不被自动覆盖
**规模/范围**: 当前 120 个视频，未来可扩展到多教练多课程

## 章程检查

*门控: 必须在阶段 0 研究前通过. 阶段 1 设计后重新检查.*

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 规范包含量化精准度指标 (原则 VIII) | ✅ 通过 | SC-001 ≥95% tech_category 准确率；SC-002 ≥85% tech_detail 准确率 |
| 无前端实现任务混入 (附加约束) | ✅ 通过 | 所有交付物为后端 API + 数据库，无 UI 代码 |
| AI 模型治理 (原则 VI) | ✅ N/A | 本功能不涉及 AI 模型推理，使用关键词规则匹配 |
| 用户数据隐私 (原则 VII) | ✅ 通过 | `video_classifications` 存储的是视频元数据（COS 路径、分类标签），不含用户个人数据 |
| 功能分支命名格式 | ✅ 通过 | `004-video-classification` |
| 测试优先 (原则 II) | ✅ 通过 | 分类服务有明确的准确率验收标准（120 视频 ground truth 对比） |

**阶段 1 设计后重新检查**: 数据模型不含敏感字段，无隐私风险。复杂度在可接受范围内（无需额外说明）。

## 项目结构

### 文档（此功能）

```
specs/004-video-classification/
├── plan.md              # 此文件
├── spec.md              # 功能规范
├── research.md          # 阶段 0 输出
├── data-model.md        # 阶段 1 输出
├── contracts/
│   └── videos-api.md    # 阶段 1 输出
└── checklists/
    └── requirements.md
```

### 源代码（新增/修改）

```
src/
├── config/
│   └── video_classification.yaml      # 新增：分类树定义（YAML）
├── models/
│   ├── __init__.py                     # 修改：导入 VideoClassification
│   └── video_classification.py        # 新增：ORM 模型
├── services/
│   └── video_classifier.py            # 新增：分类服务
├── api/
│   ├── routers/
│   │   ├── tasks.py                   # 修改：废弃 infer_action_type_hint()
│   │   └── videos.py                  # 新增：视频分类 API
│   ├── schemas/
│   │   └── video_classification.py    # 新增：请求/响应 Schema
│   └── main.py                        # 修改：注册 videos router
└── db/
    └── migrations/versions/
        └── 0005_video_classifications.py  # 新增：建表迁移
```

**结构决策**: 单一项目结构，在现有 `src/` 下扩展，遵循项目现有模式（models/services/api/db 四层分离）。

## 实施阶段

### 阶段 0 — 分类树验证（无代码）
- 人工对照 120 个视频文件名制作 ground truth 映射表（见 `research.md`）
- 验证 YAML 关键词规则对全部视频的覆盖情况

### 阶段 1 — 数据层
1. 编写 `video_classification.yaml` 分类树配置
2. 编写 `VideoClassification` ORM 模型
3. 编写 Alembic 迁移 `0005`

### 阶段 2 — 服务层
4. 实现 `VideoClassifierService`（关键词匹配 + confidence 分级）
5. 验证分类准确率（对比 research.md ground truth，目标 ≥95%/≥85%）

### 阶段 3 — API 层
6. 编写 Pydantic schemas
7. 实现 `videos.py` router（4 个端点）
8. 修改 `tasks.py` 废弃 `infer_action_type_hint()`
9. 注册 router 到 `main.py`

### 阶段 4 — 集成
10. 运行 `alembic upgrade 0005`
11. 调用 `POST /api/v1/videos/classifications/refresh` 初始化数据
