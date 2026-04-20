# Research: Feature-005 音频技术要点提炼与教学建议知识库

## 关键发现

### 1. 现有系统无 LLM API 集成

**Decision**: Feature-005 的"教学建议提炼"采用 **LLM API（OpenAI GPT）** 而非规则/正则扩展。

**Rationale**:
- 现有音频提取（TranscriptTechParser）使用正则匹配数值参数（如"保持90度"），但**无法理解语义性指导描述**（如"引拍时保持放松"）
- 正则无法捕获指导性建议的多样化表达方式
- LLM 擅长将自然语言讲解总结为结构化要点，正好匹配需求
- 第06节正手攻球已有 216 句高质量中文转录，LLM 摘要质量可预期

**Alternatives considered**:
- 扩展正则规则：覆盖率有限，维护成本高
- 关键词抽取：无法生成连贯的指导性描述

**Integration requirement**: 需新增 `OPENAI_API_KEY` 配置项 + `openai` Python 包。

---

### 2. AudioTranscript.sentences 结构（来源：代码库）

```json
[
  { "start": 0.5, "end": 2.8, "text": "肘部角度保持在90到95度之间", "confidence": 0.92 }
]
```

Feature-005 直接从 `AudioTranscript.sentences` 读取转录文本，无需重新运行 Whisper。

---

### 3. TeachingTip 不需要新表版本字段

**Decision**: `TeachingTip` 表无 `knowledge_base_version` 字段，独立存在。

**Rationale**: 澄清 Q2 已确认 TeachingTip 独立于 KB 版本审批流，直接绑定 `task_id` 即可溯源。

---

### 4. 技术阶段（TechPhase）枚举值

基于教学视频讲解规律，定义以下阶段枚举（字符串类型，不用 DB enum 保持灵活）：

| 值 | 含义 |
|----|------|
| `preparation` | 引拍/准备 |
| `contact` | 击球瞬间 |
| `follow_through` | 随挥/收拍 |
| `footwork` | 步法/移动 |
| `general` | 通用（无法分阶段） |

---

### 5. LLM 调用方式

**Decision**: 同步调用（blocking），放在 Celery worker 中执行，与现有 expert_video_task 处理流程一致。

**Prompt 设计原则**:
- 输入：完整转录文本（all sentences concatenated）
- 要求 LLM 输出 JSON 数组，每条含 `phase`、`tip_text`、`confidence`
- 先做"是否含技术讲解"判断（澄清 Q3），再做要点提炼

**Rationale**: 异步 LLM 调用复杂度更高，单视频转录文本量小（第06节 216 句），同步调用延迟可接受（≤30s）。

---

### 6. advice_generator.py 扩展方案

**Decision**: 扩展 `AdviceGenerator.generate()` 方法，在生成 `CoachingAdvice` 后查询同 action_type 的 TeachingTip，拼接为 `improvement_method` 的补充描述。

**Rationale**:
- 不改变 CoachingAdvice 数据模型（避免迁移）
- 新增一个辅助字段 `teaching_tips` 在 API 响应层拼装（或直接追加到 `improvement_method` 文本）
- 澄清 Q1：按 action_type 宽匹配，默认最多 3 条

**Alternatives considered**: 新建 CoachingAdviceTip 关联表——过度工程化，规范未要求。

---

### 7. FR-008 重新触发实现

`POST /tasks/{task_id}/extract-tips` 端点：
1. 验证 task 存在且 status=success、task_type=expert_video
2. 查询该 task 的 AudioTranscript（已存在）
3. 调用 TeachingTipExtractor（新服务）
4. 删除旧的 auto 状态 TeachingTip，保留 human 状态条目
5. 写入新提炼结果

---

### 8. 数据库迁移

需要新建 `teaching_tips` 表，对应迁移文件 `0006_teaching_tips.py`。

---

### 9. 章程合规性预检

- **原则 II (TDD)**：TeachingTipExtractor 需要单元测试（mock LLM 响应），API 端点需要合约测试
- **原则 IV (YAGNI)**：不引入 TechPhase 枚举数据库类型，用字符串存储
- **原则 V (可观测性)**：LLM 调用需记录 prompt 版本、token 数、响应时间
- **原则 VI (AI 模型治理)**：记录使用的 GPT 模型版本（可配置），推理超时需设置（默认 30s）
- **原则 VIII (精准度)**：SC-001 定义了 ≥3 条提炼要点的验证基准
