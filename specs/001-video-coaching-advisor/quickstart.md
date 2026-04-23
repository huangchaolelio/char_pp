# 快速启动指南: 视频教学分析与专业指导建议

**功能分支**: `001-video-coaching-advisor`
**日期**: 2026-04-17

## 前提条件

- Python 3.11+
- PostgreSQL 14+
- Redis 7+
- FFmpeg（系统级安装）

## 环境准备

```bash
# 1. 安装 Python 依赖
pip install fastapi uvicorn[standard] celery redis sqlalchemy psycopg2-binary \
            mediapipe opencv-python-headless ffmpeg-python pydantic alembic \
            cos-python-sdk-v5

# 2. 配置腾讯云 COS 凭证（专家视频源）
export COS_SECRET_ID=your_secret_id
export COS_SECRET_KEY=your_secret_key
export COS_REGION=ap-guangzhou
export COS_BUCKET=your-bucket-name

# 3. 启动 PostgreSQL 和 Redis（示例使用 Docker）
docker run -d --name pg -e POSTGRES_PASSWORD=secret -p 5432:5432 postgres:14
docker run -d --name redis -p 6379:6379 redis:7

# 4. 初始化数据库
alembic upgrade head

# 5. 启动 Celery Worker
celery -A app.worker worker --loglevel=info

# 6. 启动 API 服务
uvicorn app.main:app --reload --port 8000
```

## 快速验证（端到端冒烟测试）

### 步骤 1: 提交专家视频分析任务（从 COS 读取）

```bash
# 前提：教练视频已预先上传至 COS，如 coach-videos/forehand_lesson_001.mp4
# 提交分析任务，传入 COS Object Key
curl -X POST http://localhost:8000/api/v1/tasks/expert-video \
  -H "Content-Type: application/json" \
  -d '{"cos_object_key": "coach-videos/forehand_lesson_001.mp4", "notes": "正手拉球教学示范"}'

# 返回示例：{"data": {"task_id": "abc123...", "status": "pending"}}

# 查询任务状态（5 分钟内应变为 success）
curl http://localhost:8000/api/v1/tasks/abc123.../status
```

### 步骤 2: 专家审核并激活知识库

```bash
# 获取草稿版本
curl http://localhost:8000/api/v1/knowledge-base/versions

# 审核通过
curl -X POST http://localhost:8000/api/v1/knowledge-base/1.0.0/approve \
  -H "Content-Type: application/json" \
  -d '{"approved_by": "张教练", "notes": "审核通过"}'
```

### 步骤 3: 提交运动员视频，获取偏差分析

```bash
# 提交运动员视频
curl -X POST http://localhost:8000/api/v1/tasks/athlete-video \
  -F "video=@/path/to/athlete_forehand.mp4"

# 获取完整分析结果
curl http://localhost:8000/api/v1/tasks/<task_id>/result
```

## 预期输出

成功的运动员视频分析结果应包含：
- `motion_analyses` 数组（每个检测到的动作片段一条）
- 每条分析包含 `deviation_report`（偏差列表）和 `coaching_advice`（建议列表）
- 建议按 `impact_score` 降序排列
- 置信度 < 0.7 的结果标注 `is_low_confidence: true`

## 常见问题

**Q: 提交专家视频时收到 COS_OBJECT_NOT_FOUND？**
确认 `cos_object_key` 路径正确，且 COS Bucket 名称和 Region 配置与环境变量一致。
可用 COS SDK 直接验证：`python -c "from qcloud_cos import CosConfig, CosS3Client; ..."`

**Q: 任务状态一直是 pending？**
检查 Celery Worker 是否正常运行：`celery -A app.worker inspect active`

**Q: 视频提交后收到 VIDEO_QUALITY_REJECTED？**
确认视频帧率 ≥ 15fps、分辨率 ≥ 854×480。可用 FFmpeg 检查：
`ffprobe -v quiet -print_format json -show_streams video.mp4`

**Q: 没有 active 版本知识库？**
需先提交至少一段专家视频并完成专家审核（POST /knowledge-base/{version}/approve）
