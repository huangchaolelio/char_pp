# API 契约: Skill KB 到参考视频

**分支**: `003-skill-kb-to-reference-video` | **日期**: 2026-04-20

## 变更原则

本 Feature 只**新增**端点，不修改现有端点。所有新端点遵循现有错误响应结构：
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "可读描述",
    "details": {}
  }
}
```

---

## 公共 Schema

### SkillResponse
```json
{
  "id": "uuid",
  "name": "正手攻球标准提炼",
  "description": "从正手攻球教学视频批量提炼 KB",
  "action_types": ["forehand_topspin"],
  "video_source_config": {
    "type": "task_ids",
    "value": ["uuid1", "uuid2"]
  },
  "enable_audio": true,
  "audio_language": "zh",
  "extra_config": {},
  "created_by": "admin",
  "is_active": true,
  "created_at": "2026-04-20T10:00:00Z"
}
```

### ReferenceVideoResponse（嵌套在 SkillExecutionResponse 中）
```json
{
  "id": "uuid",
  "generation_status": "completed",
  "cos_key": "reference-videos/exec-{uuid}/output.mp4",
  "duration_seconds": 75.3,
  "total_dimensions": 5,
  "included_dimensions": 5,
  "error_message": null
}
```

### SkillExecutionResponse
```json
{
  "id": "uuid",
  "skill_id": "uuid",
  "status": "success",
  "kb_version": "1.2.0",
  "error_message": null,
  "rejection_reason": null,
  "approved_by": null,
  "approved_at": null,
  "created_at": "2026-04-20T10:00:00Z",
  "updated_at": "2026-04-20T10:05:00Z",
  "reference_video": {
    "id": "uuid",
    "generation_status": "completed",
    "cos_key": "reference-videos/exec-{uuid}/output.mp4",
    "duration_seconds": 75.3,
    "total_dimensions": 5,
    "included_dimensions": 5,
    "error_message": null
  }
}
```
> `reference_video` 为 null 时，execution 仍在运行中或参考视频尚未触发。

---

## 1. POST /api/v1/skills

**描述**: 创建新 Skill

### 请求体
```json
{
  "name": "正手攻球标准提炼",
  "description": "从正手攻球教学视频批量提炼 KB（可选）",
  "action_types": ["forehand_topspin"],
  "video_source_config": {
    "type": "task_ids",
    "value": ["uuid1", "uuid2"]
  },
  "enable_audio": true,
  "audio_language": "zh",
  "extra_config": {},
  "created_by": "admin"
}
```

**字段说明**:
- `video_source_config.type`: `"cos_prefix"` 或 `"task_ids"`
- `video_source_config.value`: type=cos_prefix 时为字符串前缀；type=task_ids 时为 UUID 列表
- `action_types`: 至少 1 个，枚举值：`forehand_topspin`、`backhand_push`

### 成功响应 201
```json
SkillResponse
```

### 错误响应
| 状态码 | error.code | 场景 |
|--------|------------|------|
| 409 | `SKILL_NAME_DUPLICATE` | name 与已有 Skill 重复 |
| 422 | Pydantic 验证错误 | 缺少必填字段或格式不符 |

---

## 2. GET /api/v1/skills

**描述**: 列出所有 active Skill

### 成功响应 200
```json
{
  "items": [SkillResponse, ...],
  "total": 3
}
```

---

## 3. GET /api/v1/skills/{skill_id}

**描述**: 获取单个 Skill 详情

### 成功响应 200
```json
SkillResponse
```

### 错误响应
| 状态码 | error.code | 场景 |
|--------|------------|------|
| 404 | `SKILL_NOT_FOUND` | skill_id 不存在或已软删除 |

---

## 4. PUT /api/v1/skills/{skill_id}

**描述**: 更新 Skill 配置（全量替换，非 PATCH）

### 请求体
```json
{
  "name": "正手攻球标准提炼 v2（可选，不传则不更新）",
  "description": "...",
  "action_types": ["forehand_topspin", "backhand_push"],
  "video_source_config": {
    "type": "cos_prefix",
    "value": "charhuang/tt_video/forehand/"
  },
  "enable_audio": true,
  "audio_language": "zh",
  "extra_config": {}
}
```
> 所有字段可选，只更新传入字段（内部实现为部分更新）。

### 成功响应 200
```json
SkillResponse
```

### 错误响应
| 状态码 | error.code | 场景 |
|--------|------------|------|
| 404 | `SKILL_NOT_FOUND` | skill_id 不存在 |
| 409 | `SKILL_NAME_DUPLICATE` | 更新后 name 与其他 Skill 重复 |

---

## 5. DELETE /api/v1/skills/{skill_id}

**描述**: 软删除 Skill（is_active=False），历史执行记录保留

### 成功响应 204
> 无响应体

### 错误响应
| 状态码 | error.code | 场景 |
|--------|------------|------|
| 404 | `SKILL_NOT_FOUND` | skill_id 不存在 |

---

## 6. POST /api/v1/skills/{skill_id}/execute

**描述**: 触发 Skill 执行，立即返回 execution_id，后台异步处理

### 请求体
```json
{}
```
> 无额外参数；执行参数从 Skill 配置读取。

### 成功响应 202
```json
{
  "execution_id": "uuid",
  "status": "pending",
  "message": "执行已触发，请通过 GET /api/v1/skills/executions/{execution_id} 查询进度"
}
```

### 错误响应
| 状态码 | error.code | 场景 |
|--------|------------|------|
| 404 | `SKILL_NOT_FOUND` | skill_id 不存在或已软删除 |
| 422 | `SKILL_NO_VIDEOS` | video_source_config 指向的视频列表为空 |

---

## 7. GET /api/v1/skills/executions/{execution_id}

**描述**: 查询执行状态（含参考视频嵌套信息）

> **路由注册顺序**: 此路由必须在 `GET /api/v1/skills/{skill_id}` 之前注册，避免 "executions" 被误匹配为 skill_id。

### 成功响应 200
```json
SkillExecutionResponse
```

### 错误响应
| 状态码 | error.code | 场景 |
|--------|------------|------|
| 404 | `EXECUTION_NOT_FOUND` | execution_id 不存在 |

---

## 8. POST /api/v1/skills/executions/{execution_id}/approve

**描述**: 审批通过执行结果，KB 草稿变为 active 版本

### 请求体
```json
{
  "approved_by": "admin_user_id"
}
```

### 成功响应 200
```json
SkillExecutionResponse
```
> 返回更新后的 execution，status=approved

### 错误响应
| 状态码 | error.code | 场景 |
|--------|------------|------|
| 404 | `EXECUTION_NOT_FOUND` | execution_id 不存在 |
| 409 | `EXECUTION_NOT_APPROVABLE` | execution.status 不是 success（已 approved/rejected/running 等） |
| 422 | `CONFLICT_UNRESOLVED` | KB 中存在 conflict_flag=True 的技术要点，需先解决冲突 |

---

## 9. POST /api/v1/skills/executions/{execution_id}/reject

**描述**: 驳回执行结果，KB 保持 draft 状态

### 请求体
```json
{
  "reason": "参考视频中肘部角度标注不准确，请重新提炼"
}
```

### 成功响应 200
```json
SkillExecutionResponse
```
> 返回更新后的 execution，status=rejected，rejection_reason 已填充

### 错误响应
| 状态码 | error.code | 场景 |
|--------|------------|------|
| 404 | `EXECUTION_NOT_FOUND` | execution_id 不存在 |
| 409 | `EXECUTION_NOT_APPROVABLE` | execution.status 不是 success（已 approved/rejected 等） |

---

## 错误码汇总

| error.code | HTTP 状态 | 来源异常 | 描述 |
|------------|-----------|----------|------|
| `SKILL_NOT_FOUND` | 404 | SkillNotFoundError | Skill 不存在或已软删除 |
| `SKILL_NAME_DUPLICATE` | 409 | SkillNameDuplicateError | Skill 名称已存在 |
| `SKILL_NO_VIDEOS` | 422 | SkillNoVideosError | 视频来源为空，无法执行 |
| `EXECUTION_NOT_FOUND` | 404 | ExecutionNotFoundError | 执行记录不存在 |
| `EXECUTION_NOT_APPROVABLE` | 409 | ExecutionNotApprovableError | 执行状态不允许审批/驳回 |
| `CONFLICT_UNRESOLVED` | 422 | ConflictUnresolvedError | KB 存在未解决冲突 |
