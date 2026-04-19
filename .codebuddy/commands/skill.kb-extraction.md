---
description: 将"专业教学视频 → 专业知识库"提炼流程封装为可重复执行的 Skill，完成提炼后自动生成标准参考视频供管理员审核。实现 Feature-003 的完整开发任务。
handoffs:
  - label: 查看功能规范
    agent: speckit.specify
    prompt: 查看 specs/003-skill-kb-to-reference-video/spec.md
  - label: 生成实现任务列表
    agent: speckit.tasks
    prompt: 为 Feature-003 生成任务列表
---

## 用户输入

```text
$ARGUMENTS
```

在继续之前，你**必须**考虑用户输入（如果非空）。用户输入可以是：
- 空（默认：执行完整 Feature-003 实现流程）
- `phase <A|B|C|D>`（只执行指定阶段）
- `--check`（只做现状检查，不写代码）

---

## 执行前检查

1. 读取 `specs/003-skill-kb-to-reference-video/spec.md` 确认功能规范存在。
2. 读取 `specs/003-skill-kb-to-reference-video/plan.md` 确认技术计划存在。
3. 检查 `specs/003-skill-kb-to-reference-video/tasks.md` 是否存在，如不存在则提示用户先运行 `/speckit.tasks`。
4. 读取 `specs/003-skill-kb-to-reference-video/contracts/api-changes.md` 获取 API 契约（9 个新端点的请求/响应结构）。
5. 读取以下现有代码以了解集成点：
   - `src/models/__init__.py` — 确认当前已注册的 ORM 模型
   - `src/workers/celery_app.py` — 确认 Celery include 列表
   - `src/api/main.py` — 确认已注册的 router
   - `src/services/knowledge_base_svc.py` — 了解现有 KB 服务的函数签名和异常类
   - `src/models/expert_tech_point.py` — 了解 `ExpertTechPoint` 字段（dimension, param_min/max, extraction_confidence, conflict_flag 等）
   - `src/workers/expert_video_task.py` — 了解现有 Celery 任务的执行模式（用于复用）

---

## 实现大纲

### 阶段 A：数据模型与迁移

**目标**：创建 4 个新 ORM 模型 + Alembic 迁移文件。

1. 创建 `src/models/skill.py`（`Skill` 模型）：
   - `id: uuid.UUID`（主键）
   - `name: str`（唯一，String(200)）
   - `description: Optional[str]`
   - `action_types: list[str]`（ARRAY Text）
   - `video_source_config: dict`（JSONB，存储 `{"type": "cos_prefix"|"task_ids", "value": "..."|[...]}`)
   - `enable_audio: bool = True`
   - `audio_language: str = "zh"`
   - `extra_config: dict`（JSONB，存储 dimension_expectations 等可选配置）
   - `created_by: str`
   - `is_active: bool = True`（软删除标志）
   - `created_at: datetime`
   - 关系：`executions: list[SkillExecution]`

2. 创建 `src/models/skill_execution.py`（`SkillExecution` 模型）：
   - `id: uuid.UUID`（主键）
   - `skill_id: uuid.UUID`（FK → skills.id）
   - `status: ExecutionStatus`（枚举：`pending / running / success / failed / approved / rejected`）
   - `skill_config_snapshot: dict`（JSONB，执行时的 Skill 配置快照，保证历史可追溯）
   - `kb_version: Optional[str]`（FK → tech_knowledge_bases.version，执行成功后填充）
   - `error_message: Optional[str]`
   - `rejection_reason: Optional[str]`
   - `approved_by: Optional[str]`
   - `approved_at: Optional[datetime]`
   - `created_at: datetime`
   - `updated_at: datetime`
   - 关系：`skill: Skill`、`reference_video: Optional[ReferenceVideo]`

3. 创建 `src/models/reference_video.py`（`ReferenceVideo` 模型）：
   - `id: uuid.UUID`（主键）
   - `execution_id: uuid.UUID`（FK → skill_executions.id，唯一）
   - `kb_version: str`（FK → tech_knowledge_bases.version）
   - `generation_status: str`（`pending / generating / completed / generation_failed`）
   - `cos_key: Optional[str]`（COS 对象路径，完成后填充）
   - `duration_seconds: Optional[float]`
   - `total_dimensions: int = 0`
   - `included_dimensions: int = 0`
   - `error_message: Optional[str]`
   - `created_at: datetime`
   - `updated_at: datetime`
   - 关系：`segments: list[ReferenceVideoSegment]`

4. 创建 `src/models/reference_video_segment.py`（`ReferenceVideoSegment` 模型）：
   - `id: uuid.UUID`（主键）
   - `reference_video_id: uuid.UUID`（FK → reference_videos.id，级联删除）
   - `sequence_order: int`（片段在最终视频中的顺序）
   - `dimension: str`（技术维度名称，来自 ExpertTechPoint.dimension）
   - `label_text: str`（叠加标注文字，如 `"肘部角度: 90°~120°"`）
   - `source_video_cos_key: str`（原始视频 COS 路径）
   - `source_start_ms: int`（原始视频片段开始时间，毫秒）
   - `source_end_ms: int`（原始视频片段结束时间，毫秒）
   - `extraction_confidence: float`
   - `conflict_flag: bool = False`
   - 关系：`reference_video: ReferenceVideo`

5. 更新 `src/models/__init__.py`，添加 4 个新模型的导入和 `__all__` 条目。

6. 创建 Alembic 迁移文件 `src/db/migrations/versions/0003_skill_reference_video.py`：
   - 创建 4 张表：`skills`、`skill_executions`、`reference_videos`、`reference_video_segments`
   - 添加所有必要的 FK 约束、索引（skill_executions 按 skill_id + status、reference_videos 按 execution_id）
   - `downgrade()` 方法按依赖顺序反向删除表

---

### 阶段 B：Skill CRUD 与执行触发

**目标**：实现 Skill 管理 API（5 个端点）+ 执行触发（1 个端点）。

#### B1. Pydantic Schema（`src/api/schemas/skill.py`）

参照 `src/api/schemas/knowledge_base.py` 的风格，创建：
- `SkillCreate`、`SkillUpdate`、`SkillResponse`、`SkillListResponse`
- `SkillExecuteRequest`（可空，触发时无额外参数）
- `SkillExecutionResponse`（含 `reference_video` 嵌套对象，当 reference_video 存在时展开）
- 字段严格对应 `contracts/api-changes.md` 中的请求/响应结构

#### B2. Skill 服务（`src/services/skill_svc.py`）

参照 `src/services/knowledge_base_svc.py` 的异步风格：

```python
# 基础 CRUD
async def create_skill(session, data: SkillCreate) -> Skill
async def get_skill(session, skill_id: UUID) -> Skill           # 不存在抛 SkillNotFoundError
async def list_skills(session) -> list[Skill]
async def update_skill(session, skill_id: UUID, data: SkillUpdate) -> Skill
async def delete_skill(session, skill_id: UUID) -> None         # 软删除（is_active=False）

# 执行触发
async def trigger_execution(session, skill_id: UUID) -> SkillExecution
    # 1. 加载 Skill，验证 is_active=True
    # 2. 验证 video_source 非空（抛 SkillNoVideosError）
    # 3. 创建 SkillExecution，status=pending，保存 skill_config_snapshot
    # 4. 返回 execution（Celery 任务由 router 层 enqueue）

# 执行状态查询
async def get_execution(session, execution_id: UUID) -> SkillExecution

# 审批
async def approve_execution(session, execution_id: UUID, approved_by: str) -> SkillExecution
    # 1. 加载执行记录，验证 status == success
    # 2. 检查 KB 冲突（conflict_flag=True 的 tech point 是否存在），存在则抛 ConflictUnresolvedError
    # 3. 调用 knowledge_base_svc.approve_version()
    # 4. 更新 execution status → approved，填充 approved_by/approved_at

async def reject_execution(session, execution_id: UUID, reason: str) -> SkillExecution
    # 1. 加载执行记录，验证 status == success
    # 2. 更新 execution status → rejected，保存 rejection_reason
```

自定义异常类（定义在同文件底部）：
- `SkillError`（基类）
- `SkillNotFoundError`
- `SkillNameDuplicateError`
- `SkillNoVideosError`
- `ExecutionNotFoundError`
- `ExecutionNotApprovableError`
- `ConflictUnresolvedError`

#### B3. Celery 任务存根（`src/workers/skill_execution_task.py`）

```python
@shared_task(bind=True, name="src.workers.skill_execution_task.run_skill_execution")
def run_skill_execution(self, execution_id_str: str) -> dict:
    """
    1. 加载 SkillExecution，更新 status → running
    2. 从 skill_config_snapshot 解析视频来源
    3. 对每个视频，复用 expert_video_task 的处理链路（action_classifier → tech_extractor）
       注：直接调用服务层函数，不 re-enqueue expert_video_task
    4. 汇总 ExpertTechPoints，调用 knowledge_base_svc.create_draft_version() + add_tech_points()
    5. 更新 SkillExecution：status → success，kb_version → 新版本号
    6. 自动 enqueue reference_video_task（阶段 C 任务）
    7. 异常处理：status → failed，写入 error_message
    """
```

#### B4. FastAPI Router（`src/api/routers/skills.py`）

API 路径严格按照 `contracts/api-changes.md`：

```
POST   /api/v1/skills                                  → 201
GET    /api/v1/skills                                  → 200
GET    /api/v1/skills/{skill_id}                       → 200
PUT    /api/v1/skills/{skill_id}                       → 200
DELETE /api/v1/skills/{skill_id}                       → 204
POST   /api/v1/skills/{skill_id}/execute               → 202（触发后 enqueue Celery 任务）
GET    /api/v1/skills/executions/{execution_id}        → 200（含 reference_video 嵌套）
POST   /api/v1/skills/executions/{execution_id}/approve → 200
POST   /api/v1/skills/executions/{execution_id}/reject  → 200
```

错误码映射（参照现有 router 的异常处理模式）：

| 异常 | HTTP 状态 | error.code |
|------|-----------|------------|
| `SkillNotFoundError` | 404 | `SKILL_NOT_FOUND` |
| `SkillNameDuplicateError` | 409 | `SKILL_NAME_DUPLICATE` |
| `SkillNoVideosError` | 422 | `SKILL_NO_VIDEOS` |
| `ExecutionNotFoundError` | 404 | `EXECUTION_NOT_FOUND` |
| `ExecutionNotApprovableError` | 409 | `EXECUTION_NOT_APPROVABLE` |
| `ConflictUnresolvedError` | 422 | `CONFLICT_UNRESOLVED` |

#### B5. 注册到应用

- `src/workers/celery_app.py`：在 `include` 列表中添加 `"src.workers.skill_execution_task"`
- `src/api/main.py`：添加 `from src.api.routers import skills` 并 `app.include_router(skills.router, prefix="/api/v1")`
- `src/models/__init__.py`：确认 4 个模型已正确导入（阶段 A 已完成）

---

### 阶段 C：标准参考视频生成

**目标**：实现 FFmpeg 参考视频生成 Celery 任务。

#### C1. 参考视频生成服务（`src/services/reference_video_generator.py`）

```python
async def generate_reference_video(
    session: AsyncSession,
    execution_id: uuid.UUID,
    settings: Settings,
) -> ReferenceVideo:
    """
    算法步骤：
    1. 加载 SkillExecution，获取 kb_version
    2. 从 knowledge_base_svc.get_tech_points() 获取该 KB 版本的所有技术要点
    3. 按 dimension 分组，每组取 extraction_confidence 最高的 ExpertTechPoint
    4. 对每个选中的 tech point：
       a. 从 source_video_id 对应的 AnalysisTask 获取原始视频 COS key
       b. 从 COS 下载对应视频片段到临时目录
       c. 用 FFmpeg 裁剪：ffmpeg -ss {start_s} -to {end_s} -i {input} {clip_path}
       d. 构建 drawtext 标注：
          - 正常片段：label_text = "{dimension_cn}: {param_min}~{param_max}{unit}"
          - 冲突片段：label_text += " ⚠ 冲突待审核"（黄色文字）
       e. 叠加文字：ffmpeg -i {clip} -vf "drawtext=..." {annotated_clip}
    5. 按 extraction_confidence 降序排列，超过 settings.reference_video_max_duration_s（300s）时截断
    6. 用 FFmpeg concat demuxer 拼接所有标注片段
    7. 上传至 COS，路径：{settings.reference_video_cos_prefix}exec-{execution_id}/output.mp4
    8. 创建/更新 ReferenceVideo 记录 + ReferenceVideoSegment 记录
    9. 清理临时文件（即使出错也要清理）
    """
```

**FFmpeg 命令参考**（供实现时参照）：

```bash
# 裁剪片段
ffmpeg -ss {start_s:.3f} -to {end_s:.3f} -i {input_path} -c copy {clip_path}

# 叠加文字（支持中文需指定字体文件）
ffmpeg -i {clip_path} \
  -vf "drawtext=fontfile={font_path}:text='{label}':fontcolor=white:fontsize=28:x=20:y=20:box=1:boxcolor=black@0.5" \
  -codec:a copy {annotated_path}

# 冲突片段额外叠加（黄色警告文字）
ffmpeg -i {clip_path} \
  -vf "drawtext=fontfile={font_path}:text='{label}':fontcolor=white:fontsize=28:x=20:y=20:box=1:boxcolor=black@0.5, \
       drawtext=fontfile={font_path}:text='⚠ 冲突待审核':fontcolor=yellow:fontsize=24:x=20:y=60" \
  -codec:a copy {annotated_path}

# 拼接（concat demuxer）
# 先生成 filelist.txt：每行 "file '/tmp/clip_001.mp4'"
ffmpeg -f concat -safe 0 -i filelist.txt -c copy {output_path}
```

**配置项**（需添加到 `src/config.py`）：
- `reference_video_font_path: str`（默认 `"/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"`）
- `reference_video_max_duration_s: int = 300`
- `reference_video_cos_prefix: str = "reference-videos/"`

#### C2. Celery 任务（`src/workers/reference_video_task.py`）

```python
@shared_task(bind=True, name="src.workers.reference_video_task.generate_reference_video_task")
def generate_reference_video_task(self, execution_id_str: str) -> dict:
    """
    1. 创建 ReferenceVideo 记录，generation_status = generating
    2. 调用 reference_video_generator.generate_reference_video()
    3. 成功：更新 generation_status = completed，填充 cos_key/duration/dimension counts
    4. 失败：更新 generation_status = generation_failed，写入 error_message
       注意：不修改 SkillExecution.status（KB 草稿已产出，与视频生成相互独立）
    """
```

- `src/workers/celery_app.py`：在 `include` 列表中添加 `"src.workers.reference_video_task"`

---

### 阶段 D：测试与验证

**目标**：编写契约测试 + 集成测试，验证完整流程。

#### D1. 契约测试（`tests/contract/test_skills_api.py`）

覆盖 9 个端点的请求/响应结构（参照 `contracts/api-changes.md`）：
- 正常路径：201/200/202/204 状态码 + 响应体字段完整性
- 错误路径：404、409、422 状态码 + `error.code` 字段值正确

#### D2. 集成测试（`tests/integration/test_skill_execution_flow.py`）

覆盖以下场景（使用真实 DB，不 mock Celery，直接调用任务函数）：
- **完整流程**：创建 Skill → 触发执行 → 执行成功 → 参考视频生成 → 审批通过 → KB 变 active
- **驳回流程**：执行成功 → 参考视频生成 → 驳回 → KB 保持 draft
- **冲突阻断**：KB 存在 conflict_flag=True 的 tech point → approve 返回 `CONFLICT_UNRESOLVED`
- **参考视频生成失败**：execution 保持 success，reference_video 标记 generation_failed
- **并发执行**：同一 Skill 触发两次，产出两个独立 execution 记录

---

## 实现约束

- **测试先行**：每个阶段的功能实现前，先写对应测试（TDD）
- **API 路径以合约为准**：严格按 `contracts/api-changes.md`，不自行修改路径结构
- **复用现有服务**：`knowledge_base_svc.py`（KB 版本管理）、`cos_client.py`（COS 操作）、`tech_extractor.py`（技术要点提炼）、`expert_video_task.py` 的处理逻辑
- **不修改现有端点**：`/knowledge-base/*` 和 `/tasks/*` 端点保持不变
- **临时文件清理**：FFmpeg 产生的临时片段文件，无论成功失败都必须在 `finally` 块中清理

---

## 相关文件参考

| 文件 | 说明 |
|------|------|
| `specs/003-skill-kb-to-reference-video/spec.md` | 功能规范（用户故事、验收场景、成功标准） |
| `specs/003-skill-kb-to-reference-video/plan.md` | 技术实现计划（架构决策、阶段分解） |
| `specs/003-skill-kb-to-reference-video/data-model.md` | 数据模型详细设计 |
| `specs/003-skill-kb-to-reference-video/contracts/api-changes.md` | 9 个新端点的完整请求/响应契约 |
| `specs/003-skill-kb-to-reference-video/tasks.md` | 实现任务列表（如已生成） |
| `src/services/knowledge_base_svc.py` | 现有 KB 服务（create_draft_version、approve_version 等） |
| `src/workers/expert_video_task.py` | 现有专家视频处理 Celery 任务（处理逻辑复用参考） |
| `src/models/expert_tech_point.py` | ExpertTechPoint ORM（dimension、conflict_flag 等字段） |
| `src/api/routers/knowledge_base.py` | 现有 KB router（异常处理模式参考） |
| `src/services/cos_client.py` | COS 客户端（upload/download 接口参考） |
