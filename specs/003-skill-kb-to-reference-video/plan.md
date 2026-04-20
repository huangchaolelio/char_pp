# 实施计划: Skill KB 到参考视频

**分支**: `003-skill-kb-to-reference-video` | **日期**: 2026-04-20 | **规范**: [spec.md](./spec.md)

## 摘要

在 Feature-001/002 已有的"单次上传专家视频 → KB 草稿"基础上，增加以下三层能力：

1. **Skill 实体**：持久化的可重复执行配置单元，管理员一次配置、多次执行。
2. **SkillExecution 工作流**：触发 → 运行 → success/failed → approved/rejected 的状态机，每次执行独立记录，历史可追溯。
3. **参考视频自动生成**：执行成功后，按 KB 技术维度从原始视频截取最优片段、叠加标注文字、拼接为完整参考视频并上传 COS。

技术方法：复用现有 `expert_video_task` 的处理链路（pose → segment → classify → extract），新增独立 Skill/SkillExecution/ReferenceVideo/ReferenceVideoSegment 四张表，FFmpeg 拼接参考视频，Celery 异步执行全流程。

## 技术背景

**语言/版本**: Python 3.11+
**主要依赖**:
- FastAPI 0.111.0+ — 现有 API 框架
- Celery 5.4.0+ / Redis — 现有异步任务队列
- SQLAlchemy 2.0.30+ asyncio — 现有 ORM
- FFmpeg（系统级）— 参考视频裁剪/标注/拼接
- PostgreSQL JSONB — Skill 配置快照存储

**新增表**: `skills`、`skill_executions`、`reference_videos`、`reference_video_segments`

**测试**: pytest + 契约测试（9 端点）+ 集成测试（完整执行流程）

## 章程检查

**是否创建新表？** 是（4 张）→ 需要 Alembic 迁移
**是否修改现有端点？** 否 — /tasks/* 和 /knowledge-base/* 保持不变
**是否新增 Celery 任务？** 是（2 个：skill_execution_task、reference_video_task）
**是否需要新增配置项？** 是（reference_video_font_path、max_duration_s、cos_prefix）

---

## 阶段分解

### 阶段 A：数据模型与迁移

**产出文件**:
- `src/models/skill.py`
- `src/models/skill_execution.py`
- `src/models/reference_video.py`
- `src/models/reference_video_segment.py`
- `src/models/__init__.py`（更新）
- `src/db/migrations/versions/0003_skill_reference_video.py`

**关键决策**:
- `ExecutionStatus` 枚举独立定义在 `skill_execution.py`，不复用现有 `TaskStatus`（语义不同）
- `skill_config_snapshot` 存 Skill 执行时的完整配置 JSON，保证历史可追溯（即使 Skill 后续修改）
- `ReferenceVideo` 与 `SkillExecution` 为 1:1 关系（唯一约束在 execution_id 上）
- `ReferenceVideoSegment` 级联删除（随 ReferenceVideo 删除）

### 阶段 B：Skill CRUD 与执行触发

**产出文件**:
- `src/api/schemas/skill.py`
- `src/services/skill_svc.py`
- `src/workers/skill_execution_task.py`
- `src/api/routers/skills.py`
- `src/workers/celery_app.py`（更新）
- `src/api/main.py`（更新）

**关键决策**:
- Router 层触发 Celery 任务（`.delay()`），Service 层只负责 DB 操作，保持职责分离
- `approve_execution` 调用已有的 `knowledge_base_svc.approve_version()`，不重复实现单活版本约束
- `ConflictUnresolvedError` 在 `skill_svc.py` 中重新包装（不直接暴露 KB 服务的同名异常），保持 API 层错误码一致性

**API 路径**（严格按 contracts/api-changes.md）:
```
POST   /api/v1/skills
GET    /api/v1/skills
GET    /api/v1/skills/{skill_id}
PUT    /api/v1/skills/{skill_id}
DELETE /api/v1/skills/{skill_id}
POST   /api/v1/skills/{skill_id}/execute
GET    /api/v1/skills/executions/{execution_id}
POST   /api/v1/skills/executions/{execution_id}/approve
POST   /api/v1/skills/executions/{execution_id}/reject
```

**注意**: `GET /api/v1/skills/executions/{execution_id}` 中 `executions` 是固定路径段，路由注册时需在 `GET /api/v1/skills/{skill_id}` 之前，避免 FastAPI 将 "executions" 误匹配为 skill_id。

### 阶段 C：参考视频生成

**产出文件**:
- `src/services/reference_video_generator.py`
- `src/workers/reference_video_task.py`
- `src/config.py`（更新：新增 3 个配置项）
- `src/workers/celery_app.py`（更新：添加 reference_video_task）

**关键决策**:
- FFmpeg 裁剪使用 `-c copy`（无重编码，速度快），叠加文字时才重编码
- 每个维度选择 `extraction_confidence` 最高的 `ExpertTechPoint`，再从对应 `source_video_id` 的任务中获取原始 COS key
- 片段时间戳来源：`ExpertTechPoint.transcript_segment_id` → `TechSemanticSegment.start_ms/end_ms`（Feature-002 已填充）；若无音频时间戳，则用视频分析任务的时间范围估算
- 临时文件统一放在 `/tmp/coaching-advisor/ref-{execution_id}/`，finally 块清理
- 参考视频最大时长 300s（可配置），按 confidence 降序截断超时部分

**COS 路径约定**:
```
{settings.reference_video_cos_prefix}exec-{execution_id}/output.mp4
```

### 阶段 D：测试与验证

**产出文件**:
- `tests/contract/test_skills_api.py`
- `tests/integration/test_skill_execution_flow.py`

**测试策略**:
- 契约测试：直接调用 FastAPI TestClient，mock DB session，验证响应结构
- 集成测试：使用测试 DB（与现有集成测试相同的 fixture），直接调用 Celery 任务函数（不通过 broker），mock COS 和 FFmpeg 调用

---

## 项目结构（新增文件）

```text
src/
  models/
    skill.py                        # Skill ORM
    skill_execution.py              # SkillExecution ORM + ExecutionStatus enum
    reference_video.py              # ReferenceVideo ORM
    reference_video_segment.py      # ReferenceVideoSegment ORM
  services/
    skill_svc.py                    # Skill CRUD + 执行触发 + 审批
    reference_video_generator.py    # FFmpeg 参考视频生成
  workers/
    skill_execution_task.py         # Celery: KB 提炼执行
    reference_video_task.py         # Celery: 参考视频生成
  api/
    schemas/
      skill.py                      # Pydantic schemas
    routers/
      skills.py                     # FastAPI router
  db/
    migrations/versions/
      0003_skill_reference_video.py # Alembic 迁移
tests/
  contract/
    test_skills_api.py
  integration/
    test_skill_execution_flow.py
specs/003-skill-kb-to-reference-video/
  contracts/
    api-changes.md
  data-model.md
  plan.md
  spec.md
  tasks.md
```

---

## 性能目标

| 场景 | 目标 |
|------|------|
| Skill 执行触发延迟 | < 2s |
| 参考视频生成（5 维度 × 15s） | ≤ 5 分钟 |
| KB 提炼（3 个已分析任务，复用 ExpertTechPoints） | ≤ 30s |

---

## 迁移版本说明

| 迁移文件 | 版本 | 内容 |
|----------|------|------|
| 0001_initial_schema.py | v1 | 初始表结构 |
| 0002_audio_enhanced_kb_extraction.py | v2 | Feature-002 音频相关表 |
| **0003_skill_reference_video.py** | **v3** | **Feature-003：skills 等 4 张新表** |
