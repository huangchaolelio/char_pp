# 任务: Feature-005 音频技术要点提炼与教学建议知识库

**输入**: 来自 `/specs/005-audio-kb-coaching-tips/` 的设计文档
**前置条件**: plan.md ✅、spec.md ✅、research.md ✅、data-model.md ✅、contracts/teaching-tips-api.md ✅

---

## 阶段 1: 基础设施（阻塞前置）

**目的**: 数据模型、配置、依赖就绪，是所有用户故事的先决条件

**⚠️ 关键**: 在此阶段完成之前，无法开始任何用户故事工作

- [x] T001 [P] 创建 `src/models/teaching_tip.py`：`TeachingTip` ORM 模型（id UUID PK, task_id FK, action_type, tech_phase, tip_text, confidence, source_type DEFAULT 'auto', original_text nullable, created_at, updated_at）
- [x] T002 修改 `src/models/__init__.py`：注册 `TeachingTip`，确保 Alembic 能发现该模型（依赖 T001）
- [x] T003 创建 `src/db/migrations/versions/0006_teaching_tips.py`：Alembic 迁移，建表 + 3 个索引（ix_teaching_tips_task_id, ix_teaching_tips_action_type, ix_teaching_tips_source_type）（依赖 T002）
- [x] T004 [P] 修改 `src/config.py`：添加 `OPENAI_API_KEY`（必填，无默认值）、`OPENAI_MODEL`（默认 `gpt-4o-mini`）两个配置项
- [x] T005 修改 `pyproject.toml`：在依赖项中添加 `openai>=1.0`；在 coaching conda 环境中安装（依赖 T004）

**检查点**: T001-T005 全部完成后，数据库表已建，配置已就绪，可开始 US1/US2/US3 并行

---

## 阶段 2: US1 — 从教练讲解中提炼技术要点描述 (优先级: P1) 🎯 MVP

**目标**: 完成音频转录后自动调用 LLM 提炼教学建议，存入 teaching_tips 表

**独立测试**: 用第06节正手攻球 task_id 触发 extract-tips，查看 teaching_tips 表，确认 ≥3 条 source_type='auto' 的条目，内容与正手攻球教学相关

### US1 测试（TDD — 先写测试，确认失败后再实现）⚠️

- [x] T006 [P] [US1] 在 `tests/unit/test_teaching_tip_extractor.py` 中编写单元测试，mock openai 客户端，覆盖 3 个场景：
  1. 转录含技术讲解 → 返回 ≥1 条 TeachingTip 列表
  2. 转录无技术讲解（纯示范）→ 返回空列表，reason="无技术讲解内容"
  3. LLM 调用超时（30s）→ 降级返回空列表，不抛异常

### US1 实施

- [x] T007 [US1] 实现 `src/services/teaching_tip_extractor.py`：`TeachingTipExtractor` 类，含以下方法（依赖 T005, T006 测试先行）：
  - `extract(transcript_sentences: list[dict], action_type: str, task_id: UUID) -> list[TeachingTipData]`
  - Step 1：构造 prompt 调用 GPT，判断是否含技术讲解（is_technical: bool）
  - Step 2：若 is_technical=True，调用 GPT 按 tech_phase 分组提炼要点，返回结构化列表
  - Step 3：LLM 超时（30s）或异常时降级返回 []，记录警告日志
  - LLM 模型从 `settings.OPENAI_MODEL` 读取
- [x] T008 [US1] 修改 `src/workers/expert_video_task.py`：在音频转录成功保存 AudioTranscript 之后，自动调用 `TeachingTipExtractor.extract()`，将结果批量写入 teaching_tips 表；提炼失败（返回空或异常）时仅记录日志，不阻断主流程（依赖 T007）
- [x] T009 [US1] 端到端验证：用第06节正手攻球 task_id 调用 `POST /tasks/{task_id}/extract-tips`，确认数据库中出现 ≥3 条 teaching_tips 条目，检查 tip_text 内容可读且 tech_phase 字段分布合理（依赖 T007, T008, T017）

**检查点**: US1 完全功能化——提炼服务可独立运行，Worker 自动触发，结果写入 DB

---

## 阶段 3: US2 — 教学建议在运动员指导中被调用 (优先级: P1)

**目标**: 运动员改进建议中附加匹配的 TeachingTip 文字，建议内容包含量化偏差 + 定性指导

**独立测试**: 提交正手攻球运动员视频，获取 GET /tasks/{id}/result，验证 coaching_advice 中含 `teaching_tips` 字段，且有至少 1 条 tip_text 内容可读

### US2 测试（合约测试）⚠️

- [x] T013 [P] [US2] 在 `tests/contract/test_teaching_tips_api.py` 中为 `GET /tasks/{task_id}/result` 编写合约测试：验证响应包含 `coaching_advice[].teaching_tips` 字段，类型为数组，每项含 `tip_text`、`tech_phase`、`source_type`

### US2 实施

- [x] T010 [US2] 修改 `src/services/advice_generator.py`：在 `generate()` 中按 action_type 宽匹配查询 teaching_tips 表，human 优先，最多取 `settings.MAX_TEACHING_TIPS`（默认 3）条；将 tip_text 追加到 `improvement_method` 字段末尾，格式：
  ```
  {原有改进方法}

  💡 教练建议：
  • {tip_text_1}（来源：{task视频名}）
  ```
  无匹配时不报错，直接返回原有建议（依赖 T007）
- [x] T011 [US2] 修改 `src/api/schemas/`（CoachingAdvice 相关 schema）：`CoachingAdviceItem` 新增 `teaching_tips: list[TeachingTipRef]` 字段（`TeachingTipRef` 含 tip_text, tech_phase, source_type），默认为空列表（依赖 T010）
- [x] T012 [US2] 修改 `src/api/routers/tasks.py` 的结果查询端点：在 CoachingAdvice 结果填充时，加载对应的 TeachingTip 列表填充到 `teaching_tips` 字段（依赖 T011）

**检查点**: US1 + US2 均独立可运行——运动员分析结果包含文字教学建议

---

## 阶段 4: US3 — 人工审核与编辑教学建议 (优先级: P2) + API 层完善

**目标**: 提供 CRUD API 供管理员查看/编辑/删除 teaching_tips，并实现 extract-tips 重新触发端点

**独立测试**: 通过 PATCH /teaching-tips/{id} 编辑一条 auto 条目，确认 source_type 变为 human，original_text 保留原始内容；再次生成运动员建议，使用 human 版本

### US3 实施

- [x] T014 [P] [US3] 创建 `src/api/schemas/teaching_tip.py`：定义 `TeachingTipResponse`、`TeachingTipListResponse`、`TeachingTipPatch`（tip_text 可选更新）、`ExtractTipsResponse`（accepted 状态202） Pydantic schemas
- [x] T015 [US3] 创建 `src/api/routers/teaching_tips.py`：4 个端点（依赖 T014）：
  - `GET /teaching-tips`：按 action_type, tech_phase, source_type, task_id 过滤，返回列表
  - `PATCH /teaching-tips/{id}`：更新 tip_text，自动设 source_type='human'，保存旧文本到 original_text
  - `DELETE /teaching-tips/{id}`：软删除或硬删除（返回 204）
  - `POST /tasks/{task_id}/extract-tips`：验证任务存在且有音频转录，异步触发重新提炼（返回 202）
- [x] T016 [US3] 修改 `src/api/main.py`：注册 teaching_tips router，prefix="/api/v1"（依赖 T015）
- [x] T017 [US3] 实现 `POST /tasks/{task_id}/extract-tips` 业务逻辑（依赖 T007, T015）：
  1. 查询 AudioTranscript by task_id，若不存在返回 422
  2. 删除该 task_id 下 source_type='auto' 的旧条目
  3. 保留 source_type='human' 条目不变
  4. 调用 `TeachingTipExtractor.extract()` 写入新 auto 条目
  5. 返回 202 Accepted + `{"task_id": ..., "status": "triggered", "preserved_human_count": N}`

**检查点**: US1 + US2 + US3 全部可独立运行，完整 CRUD + 重触发 API 就绪

---

## 阶段 5: 收尾与横切关注点

**目的**: 可观测性、测试覆盖率、端到端验证

- [x] T018 [P] 结构化日志：在 `TeachingTipExtractor` 中为每次 LLM 调用记录：`model_version`、`prompt_tokens`、`completion_tokens`、`elapsed_ms`、`tip_count`、`task_id`、`is_technical` 判断结果
- [x] T019 运行完整测试套件（`pytest tests/`），确认无回归；补充 T006 单元测试覆盖边界情况（action_type 宽匹配逻辑，confidence 过滤）
- [x] T020 端到端验证（对应 SC-001 ~ SC-005）：
  1. 第06节正手攻球 → 触发提炼 → 验证 ≥3 条 teaching_tips（SC-001）
  2. 提交运动员正手视频 → 获取 result → 验证 coaching_advice 含文字建议（SC-002）
  3. PATCH 编辑一条 tip → 再次分析 → 确认使用 human 版本（SC-003）
  4. 验证纯示范视频 → 无 teaching_tips 生成（SC-004）
  5. 计时提炼过程 ≤30s（SC-005）

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **阶段 1（基础）**: 无依赖，立即开始，阻塞所有后续阶段
- **阶段 2（US1）**: 依赖阶段 1 完成
- **阶段 3（US2）**: 依赖阶段 1 完成；T010 依赖 T007（TeachingTipExtractor 已实现）
- **阶段 4（US3）**: 依赖阶段 1 完成；T017 依赖 T007
- **阶段 5（收尾）**: 依赖阶段 2、3、4 全部完成

### 用户故事间依赖

- **US2（AdviceGenerator）**: 逻辑上依赖 US1 已有 teaching_tips 数据，但实现可独立完成（先写查询逻辑，测试时用 fixture 数据）
- **US3（CRUD API）**: 完全独立，可与 US1/US2 并行实现
- **T009（验证）**: 依赖 T017（extract-tips 端点）就绪

### 并行机会

| 可并行的任务组 | 说明 |
|----------------|------|
| T001 + T004 | 模型文件 vs 配置文件，无依赖 |
| T006 + T014 | 单元测试 vs Schema，无依赖 |
| T013 + T014 | 合约测试 vs Schema，无依赖 |
| US2（T010-T012）+ US3（T014-T017）| 阶段 3/4 可并行（基础完成后）|
| T018 + T019 | 日志 vs 测试，无依赖 |

---

## 注意事项

- [P] 标记 = 不同文件，无依赖关系，可并行
- T006 **必须在 T007 之前完成**（TDD），确认测试失败后再开始实现
- `source_type` 状态迁移：auto → human 不可逆；重触发只删 auto，保留 human
- LLM 调用必须设置 30s 超时，失败时降级为空列表，不中断 Worker 主流程
- `tech_phase` 使用字符串（不创建 DB enum）：preparation/contact/follow_through/footwork/general
- `coaching_advice` 表不修改 schema，只扩展 `improvement_method` 文本内容
- OPENAI_API_KEY 为必填配置，启动时缺少则 TeachingTipExtractor 初始化时报 ConfigError
