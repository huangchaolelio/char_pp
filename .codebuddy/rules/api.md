---
alwaysApply: false
paths: src/api/**/*.py
---

# API 设计规范

- 版本前缀统一：`/api/v1/`
- 每个路由文件对应一个资源（coaches, videos, tasks...），禁止混搭不同资源
- 分页参数统一：`page`（从 1 开始）+ `page_size`（默认 20，最大 100）
- 响应体统一包装：成功返回数据本身，或 `{"data": [...], "total": N}`

# 现有路由模块（9 个）

| 文件 | 前缀 | 说明 |
|------|------|------|
| `tasks.py` | `/api/v1/tasks` | 任务提交、查询、删除（含全量分页 Feature-012） |
| `knowledge_base.py` | `/api/v1/knowledge-base` | 知识库版本管理 |
| `videos.py` | `/api/v1/videos` | 视频分类刷新（扫全量 COS_VIDEO_ALL_COCAH） |
| `classifications.py` | `/api/v1/classifications` | COS 扫描分类（Feature-008） |
| `coaches.py` | `/api/v1/coaches` | 教练 CRUD |
| `standards.py` | `/api/v1/standards` | 技术标准查询与构建（Feature-010） |
| `diagnosis.py` | `/api/v1/diagnosis` | 运动员同步诊断（60s 超时，Feature-011） |
| `teaching_tips.py` | `/api/v1/teaching-tips` | 教学建议（Feature-005） |
| `calibration.py` | `/api/v1/calibration` | 多教练知识库对比（Feature-006） |

# 错误响应

路由层统一捕获服务层异常，转换为 `HTTPException`：
- `ValueError` → 400
- `NotFoundException` → 404
- 未预期异常 → 500（含 logging）
