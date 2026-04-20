# 快速入门: 音频增强型教练视频技术知识库提取

**分支**: `002-audio-enhanced-kb-extraction` | **日期**: 2026-04-19

## 前置条件

```bash
# 1. 确认在正确分支
git checkout 002-audio-enhanced-kb-extraction

# 2. 安装新依赖
pip install openai-whisper==20231117 jieba==0.42.1

# 3. 确认 ffmpeg 可用
ffmpeg -version | head -1

# 4. 下载 Whisper small 中文模型（首次运行自动下载，约 244MB）
python -c "import whisper; whisper.load_model('small')"

# 5. 执行数据库迁移（新增两张表 + 现有表字段扩展）
alembic upgrade head
```

## 运行开发环境

```bash
# 启动服务（与现有方式相同）
uvicorn src.api.main:app --reload --port 8000

# 启动 Celery worker（同现有）
celery -A src.workers.celery_app worker --loglevel=info
```

## 验证音频增强功能

```bash
# 1. 提交专家视频任务（开启音频分析）
curl -X POST http://localhost:8000/api/v1/tasks/expert-video \
  -H "Content-Type: application/json" \
  -d '{"video_cos_key": "your-test-video.mp4", "enable_audio_analysis": true}'

# 返回示例: {"task_id": "abc-123", "status": "pending"}

# 2. 轮询进度（长视频时可见进度更新）
curl http://localhost:8000/api/v1/tasks/abc-123
# 返回: {"status": "processing", "progress_pct": 35.0, "processed_segments": 2, "total_segments": 6}

# 3. 任务完成后查看结果
curl http://localhost:8000/api/v1/tasks/abc-123/result

# 检查结果中的音频分析字段:
# - audio_analysis.quality_flag = "ok" 表示音频正常使用
# - tech_points 中含 source_type = "audio" 或 "visual+audio" 的条目
# - conflicts 数组如非空，需管理员审核
```

## 验证音频回退模式

```bash
# 提交无音频的视频（或明确禁用音频分析）
curl -X POST http://localhost:8000/api/v1/tasks/expert-video \
  -H "Content-Type: application/json" \
  -d '{"video_cos_key": "silent-video.mp4", "enable_audio_analysis": true}'

# 任务完成后检查:
# - audio_analysis.quality_flag = "silent" 或 "low_snr"
# - audio_fallback_reason = "音频质量不足，已使用纯视觉分析模式"
# - 所有 tech_points.source_type = "visual"（纯视觉结果）
```

## 运行测试

```bash
# 单元测试（无需外部服务）
pytest tests/unit/test_keyword_locator.py -v
pytest tests/unit/test_transcript_tech_parser.py -v
pytest tests/unit/test_kb_merger.py -v

# 集成测试（需要 PostgreSQL + Redis）
pytest tests/integration/test_audio_enhanced_kb_extraction.py -v -m integration

# 全部测试
pytest -v
```

## 关键配置项（.env）

```bash
# 新增配置项（现有 .env 无需修改即可运行，以下为可选覆盖）
WHISPER_MODEL=small                    # tiny/base/small/medium（默认 small）
WHISPER_DEVICE=auto                    # auto/cpu/cuda（默认 auto：有 GPU 自动用 cuda，否则 cpu）
AUDIO_KEYWORD_FILE=config/keywords/tech_hint_keywords.json  # 关键词词表路径
AUDIO_PRIORITY_WINDOW_S=3.0            # 关键词前后优先窗口（秒，默认 3.0）
AUDIO_SNR_THRESHOLD_DB=10.0            # 低于此值触发音频质量不足回退
AUDIO_CONFLICT_THRESHOLD_PCT=0.15      # 参数差超过 15% 触发冲突标注
LONG_VIDEO_SEGMENT_DURATION_S=300      # 长视频分段时长（秒，默认 300=5分钟）
```
