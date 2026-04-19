# 研究报告: 音频增强型教练视频技术知识库提取

**分支**: `002-audio-enhanced-kb-extraction` | **日期**: 2026-04-19

## 决策 1: 语音识别方案选型

**Decision**: 使用 OpenAI Whisper（本地推理，`small` 中文模型）

**Rationale**:
- 普通话识别准确率在教学场景（低噪音、普通话标准）可达 90%+ 字符准确率
- 完全本地运行，无外部 API 依赖，符合数据隐私约束（原则 VII）
- `small` 模型（244M 参数）在 CPU 上约 4× 实时速度，60 分钟视频转录 ≤ 15 分钟
- 返回带时间戳的句子级分割，直接满足关键词定位需求
- 现有 Python 3.11+ 环境兼容，pip 安装

**Alternatives considered**:
| 方案 | 拒绝原因 |
|------|---------|
| 百度/阿里云 ASR API | 引入外部依赖和费用；教练视频属于敏感数据，不宜上传第三方 |
| Whisper `large` 模型（1.5GB） | CPU 推理太慢（约 1× 实时），60 分钟视频需 60+ 分钟，不满足 SC-004 |
| Whisper `tiny` 模型 | 中文准确率约 75%，低于 SC-005 要求的 80% |
| Faster-Whisper（CTranslate2） | 速度提升但引入额外编译依赖（C++），增加部署复杂性；`small` 已满足速度需求 |

---

## 决策 2: 音频提取工具

**Decision**: 使用 `ffmpeg`（现有系统依赖）通过 subprocess 调用提取 WAV

**Rationale**:
- 项目已依赖 ffmpeg（用于视频验证和转码），无新依赖
- 命令：`ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 -f wav output.wav`（单声道 16kHz，Whisper 标准输入格式）
- 提取速度 >> 实时，不成为瓶颈

**Alternatives considered**:
- `pydub`/`librosa`：需额外依赖，且底层仍调用 ffmpeg，无实质增益

---

## 决策 3: 关键词定位方案

**Decision**: 基于可配置词表的精确/模糊匹配（不使用大模型语义理解）

**Rationale**:
- 运动技术教学场景的提示词高度固定（"示范"、"注意看"、"标准动作"、"这一拍"等约 20-30 词）
- 纯字符串匹配延迟 < 1ms，远满足 < 100ms 目标
- 词表存储在 `config/keywords/tech_hint_keywords.json`，管理员可扩展
- 避免引入 LLM 推理延迟（大模型调用 100ms-1s+ / 句）和不确定性

**实现细节**:
- 匹配策略：直接 `in` 字符串匹配（支持多词命中加权）
- 时间窗口：命中词前后 3s 为高优先级区间（可配置）
- 多个命中区间合并（overlapping merge），避免重复分析

**Alternatives considered**:
- Sentence-BERT 语义相似度：精度更高但引入大模型推理，违反简洁性原则（IV）
- 正则表达式：对中文短语无优势，词表匹配更直观

---

## 决策 4: 文本技术要点解析策略

**Decision**: 规则 + 正则表达式解析，提取数值区间和维度关键词

**Rationale**:
- 教练技术描述有强规律性模式，如："[部位]保持[数值][单位]"、"角度在[N]到[M]度之间"
- 正则模式：`(\d+)[°度]?\s*[-到至~]\s*(\d+)[°度]` 捕获数值区间
- 部位关键词词表（肘、腕、膝、重心等）映射到 `dimension` 枚举
- 无法解析时输出置信度 = 0.3（标注为"低置信度语音来源"）

**已设计的正则模式集**:
```python
NUMERIC_RANGE  = r'(\d+(?:\.\d+)?)\s*[°度]?\s*[-到至~]\s*(\d+(?:\.\d+)?)\s*[°度]?'
SINGLE_NUMERIC = r'([保持维持保])[^\d]*(\d+(?:\.\d+)?)\s*[°度厘米cm]'
BODY_PART_MAP  = {"肘": "elbow_angle", "腕": "wrist_angle", "重心": "weight_transfer", "膝": "knee_angle"}
```

**Alternatives considered**:
- 大模型抽取（GPT/Claude）：精度更高但违反本地部署约束和简洁性原则
- 依存句法分析：过于复杂，对乒乓球领域无标注语料支持，YAGNI

---

## 决策 5: 长视频分段策略

**Decision**: 在 Celery 任务内循环分段处理，每段 5 分钟（300s），完成后更新进度字段

**Rationale**:
- 复用现有单段处理管道（无需重构），仅外套循环
- 每段处理完成后 `UPDATE analysis_tasks SET progress_pct = X`，满足 SC-004 ≤ 30s 延迟
- Celery 任务总超时放宽为 `hard_time_limit = None`（或设为 2h），`soft_time_limit` 仅在单段内生效
- 临时文件（音频 WAV、片段视频）按段清理，控制磁盘占用

**进度计算**:
```
progress_pct = (completed_segments / total_segments) × 100
total_segments = ceil(video_duration_seconds / 300)
```

**Alternatives considered**:
- Celery chord/chain 拆分为多个子任务：增加 Redis 消息复杂度，协调失败难以恢复；单任务循环更简单
- 流式处理（边下载边处理）：COS SDK 不支持流式，需额外工程，YAGNI

---

## 决策 6: 知识库合并与冲突阈值

**Decision**: 同维度参数差 > 15% 触发冲突标注；同向且差值 ≤ 15% 自动合并取均值

**Rationale**:
- 15% 参数误差阈值参考现有 `tech_extractor.py` 中 `swing_trajectory` 的 ±15% 范围设定，保持一致性
- 自动合并减少人工干预，仅在真实分歧时提示审核
- 合并后 `source_type = "visual+audio"`；单来源分别标 `"visual"` 或 `"audio"`

**冲突示例**: 视觉提取 elbow_angle ideal=105°，音频解析 ideal=115°，差值 = 9.5%（< 15%）→ 自动合并 ideal=110°；若音频为 125°（差值 19%）→ 标注冲突，写入两条记录

---

## 已解决的 NEEDS CLARIFICATION 项

（本规范无 [NEEDS CLARIFICATION] 标记，本节记录规划阶段的隐性假设确认）

| 假设 | 确认状态 | 依据 |
|------|---------|------|
| 音频识别语言默认普通话 | ✅ 确认 | spec.md 假设章节明确；Whisper `language="zh"` 参数固定 |
| 字幕格式以 SRT/内嵌流为主 | ✅ 确认 | `ffprobe` 可探测字幕流；SRT 可直接解析 |
| 关键词词表初期不依赖大模型 | ✅ 确认 | 原则 IV 简洁性约束；词表初版覆盖乒乓球教学常用提示词 |
| 音频临时文件不持久化 | ✅ 确认 | 原则 VII 数据隐私；WAV 文件处理后立即删除 |
| Whisper 模型文件 Git LFS 管理 | ✅ 确认 | 原则 AI/ML 约束；`docs/models/whisper-small-zh.md` 登记 |
