# 任务: Skill KB 到参考视频

**输入**: 来自 `/specs/003-skill-kb-to-reference-video/` 的设计文档
**前置条件**: spec.md ✅, plan.md ✅, data-model.md ✅, contracts/api-changes.md ✅

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1~US4）
- 描述中包含确切的文件路径

---

## 阶段 A: 数据模型与迁移

**目的**: 创建 4 个新 ORM 模型 + Alembic 迁移文件，所有后续阶段依赖此阶段完成

**⚠️ 关键**: 在此阶段完成之前，无法开始任何 Service / Router 工作

- [ ] T001 创建 `src/models/skill.py`：`Skill` ORM，字段包含 id(UUID PK)、name(String(200) UNIQUE)、description、action_types(ARRAY Text)、video_source_config(JSONB)、enable_audio(bool=True)、audio_language(str="zh")、extra_config(JSONB)、created_by、is_active(bool=True)、created_at；relationship executions
- [ ] T002 [P] [US1] 创建 `src/models/skill_execution.py`：`SkillExecution` ORM + `ExecutionStatus` 枚举（pending/running/success/failed/approved/rejected），字段包含 id、skill_id(FK→skills.id)、status、skill_config_snapshot(JSONB)、kb_version(FK→tech_knowledge_bases.version, NULLABLE)、error_message、rejection_reason、approved_by、approved_at、created_at、updated_at
- [ ] T003 [P] [US2] 创建 `src/models/reference_video.py`：`ReferenceVideo` ORM，字段包含 id、execution_id(FK→skill_executions.id UNIQUE)、kb_version(FK)、generation_status(pending/generating/completed/generation_failed)、cos_key(NULLABLE)、duration_seconds(NULLABLE)、total_dimensions(int=0)、included_dimensions(int=0)、error_message(NULLABLE)、created_at、updated_at；relationship segments
- [ ] T004 [P] [US2] 创建 `src/models/reference_video_segment.py`：`ReferenceVideoSegment` ORM，字段包含 id、reference_video_id(FK→reference_videos.id CASCADE)、sequence_order、dimension、label_text、source_video_cos_key、source_start_ms、source_end_ms、extraction_confidence、conflict_flag(bool=False)
- [ ] T005 更新 `src/models/__init__.py`：导入 Skill、SkillExecution、ReferenceVideo、ReferenceVideoSegment 并加入 `__all__`
- [ ] T006 创建 Alembic 迁移 `src/db/migrations/versions/0003_skill_reference_video.py`：upgrade() 按顺序建表 skills → skill_executions → reference_videos → reference_video_segments，添加 FK 约束和索引（ix_skill_executions_skill_status、ix_reference_videos_execution_id）；downgrade() 反向删表

**检查点**: `alembic upgrade head` 无报错，4 张新表存在 — 可开始阶段 B

---

## 阶段 B: Skill CRUD 与执行触发

**目的**: 实现 9 个新端点的完整服务层 + Router

**依赖**: 阶段 A 完成

### 阶段 B 的测试（先编写，确保在实现前失败）

- [ ] T007 [P] [US1/US4] 在 `tests/contract/test_skills_api.py` 中编写契约测试（桩）：覆盖 9 个端点的正常路径（状态码 + 响应字段完整性）和错误路径（404/409/422 + error.code 值）；此时测试应全部失败（路由未注册）
- [ ] T008 [P] [US1] 在 `tests/integration/test_skill_execution_flow.py` 中编写集成测试（桩）：覆盖完整流程、驳回流程、冲突阻断 3 个场景；此时测试应全部失败

### 阶段 B 的实现

- [ ] T009 [US4] 创建 `src/api/schemas/skill.py`：`SkillCreate`、`SkillUpdate`（所有字段可选）、`SkillResponse`、`SkillListResponse`、`SkillExecuteResponse`（202）、`ReferenceVideoResponse`（嵌套）、`SkillExecutionResponse`（含嵌套 reference_video），字段严格对应 contracts/api-changes.md
- [ ] T010 [US1/US3/US4] 创建 `src/services/skill_svc.py`：实现 create_skill、get_skill、list_skills、update_skill、delete_skill(软删除)、trigger_execution、get_execution、approve_execution（调用 knowledge_base_svc.approve_version）、reject_execution；文件底部定义 SkillError/SkillNotFoundError/SkillNameDuplicateError/SkillNoVideosError/ExecutionNotFoundError/ExecutionNotApprovableError/ConflictUnresolvedError 异常类
- [ ] T011 [US1] 创建 `src/workers/skill_execution_task.py`：`run_skill_execution` Celery 任务，流程：加载执行记录 → 解析视频来源 → 复用 expert_video_task 服务层处理链路 → create_draft_version + add_tech_points → status=success + kb_version → enqueue reference_video_task → 异常 status=failed
- [ ] T012 [US1/US3/US4] 创建 `src/api/routers/skills.py`：注册 9 个端点（注意 GET /executions/{id} 在 GET /{skill_id} 之前注册）；异常 → HTTP 状态码映射严格按 contracts/api-changes.md；触发执行端点在 Service 返回 execution 后调用 `run_skill_execution.delay(str(execution.id))`
- [ ] T013 更新 `src/workers/celery_app.py`：在 include 列表中添加 `"src.workers.skill_execution_task"` 和 `"src.workers.reference_video_task"`
- [ ] T014 更新 `src/api/main.py`：导入 skills router 并注册 `app.include_router(skills.router, prefix="/api/v1")`
- [ ] T015 [US1/US3/US4] 运行 `pytest tests/contract/test_skills_api.py -v`，修复直至全部通过

---

## 阶段 C: 参考视频生成

**目的**: 实现 FFmpeg 参考视频生成服务 + Celery 任务

**依赖**: 阶段 A 完成（阶段 B 并行进行中或已完成）

### 阶段 C 的测试（先编写，确保在实现前失败）

- [ ] T016 [P] [US2] 在 `tests/unit/test_reference_video_generator.py` 中编写单元测试：覆盖按 dimension 分组取最高置信度、时长超限截断（> max_duration_s）、label_text 生成（正常/冲突两种格式）；mock COS 和 FFmpeg subprocess 调用
- [ ] T017 [P] [US2] 在 `tests/integration/test_skill_execution_flow.py` 中补充参考视频生成相关场景：生成成功、生成失败（execution 保持 success）、并发执行

### 阶段 C 的实现

- [ ] T018 [US2] 在 `src/config.py` 中新增配置项：`reference_video_font_path`（默认 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`）、`reference_video_max_duration_s`（默认 300）、`reference_video_cos_prefix`（默认 `"reference-videos/"`）
- [ ] T019 [US2] 创建 `src/services/reference_video_generator.py`：`generate_reference_video(session, execution_id, settings)` 实现完整 FFmpeg 流程：加载 KB tech points → 按 dimension 取最高 confidence → COS 下载片段 → FFmpeg 裁剪（-c copy）→ FFmpeg drawtext 标注（冲突片段额外黄字）→ confidence 降序排列 + 时长截断 → FFmpeg concat 拼接 → COS 上传 → 创建 ReferenceVideo + Segments 记录 → finally 清理临时文件
- [ ] T020 [US2] 创建 `src/workers/reference_video_task.py`：`generate_reference_video_task` Celery 任务，流程：创建 ReferenceVideo(status=generating) → 调用 reference_video_generator → 成功更新 completed + cos_key → 失败更新 generation_failed + error_message（不修改 SkillExecution.status）
- [ ] T021 [US2] 运行 `pytest tests/unit/test_reference_video_generator.py -v`，修复直至全部通过

---

## 阶段 D: 集成测试与端到端验证

**目的**: 完整流程验证，确保所有场景通过

**依赖**: 阶段 B + 阶段 C 完成

- [ ] T022 [US1/US2/US3] 运行 `pytest tests/integration/test_skill_execution_flow.py -v`，确保以下 5 个场景全部通过：
  1. 完整流程（创建 → 执行 → success → 参考视频 → approve → KB active）
  2. 驳回流程（success → 参考视频 → reject → KB 保持 draft）
  3. 冲突阻断（KB 有 conflict_flag=True → approve 返回 CONFLICT_UNRESOLVED）
  4. 参考视频生成失败（execution 保持 success，reference_video 为 generation_failed）
  5. 并发执行（同一 Skill 触发两次 → 两个独立 execution）
- [ ] T023 [US1] 运行全量测试 `pytest tests/ -v --tb=short`，确保现有测试（Feature-001/002）不退化，新增测试全部通过
- [ ] T024 提交所有改动，git commit 信息包含 Feature-003 标识

---

## 依赖关系总结

```
T001-T004 (模型) → T005 (模型注册) → T006 (迁移)
                                         ↓
T007-T008 (测试桩)    T009 (schemas)   T016-T017 (C 测试桩)
                         ↓                  ↓
                      T010 (skill_svc) → T011 (execution task)
                         ↓
                      T012 (router) → T013 (celery_app) → T014 (main.py)
                         ↓
                      T015 (契约测试通过)
                                            ↓
                                T018 (config) → T019 (generator) → T020 (ref task)
                                                     ↓
                                                T021 (单元测试通过)
                                                          ↓
                                               T022 → T023 → T024
```

## 完成标准

- [ ] `pytest tests/contract/test_skills_api.py` — 9 个端点契约全部通过
- [ ] `pytest tests/integration/test_skill_execution_flow.py` — 5 个集成场景全部通过
- [ ] `pytest tests/ -x` — 无退化，全量测试通过
- [ ] `alembic upgrade head` — 迁移无报错
