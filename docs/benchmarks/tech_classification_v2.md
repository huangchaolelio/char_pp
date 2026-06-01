# TechClassifier V2 准确率基线（Feature-023）

> **状态**：启发式 lower bound 已完成（2026-05-31，T071 阶段一）；人工标注阶段二待排期

## 1. 模型 / 版本

| 项 | 值 |
|----|-----|
| Feature | 023 — tech-classification-rebuild |
| 模型 | `TechClassifier V2`（`src/services/tech_classifier.py`） |
| 字典版本 | `tech_actions` 56 行 v2（迁移 `0022_tech_taxonomy_rebuild` + Path 1' 拓展）；56 行 = 35 distinct action × 9 L3 桶（含 21 个跨手部重名 action） |
| 规则文件 | `config/tech_classification_rules.json` |
| LLM 兜底 | Venus Proxy 优先 → OpenAI fallback；JSON mode + 字典 enum 块强约束 |

## 2. 评估方法

- 评估脚本：[scripts/eval_v2_accuracy.py](../../specs/023-tech-classification-rebuild/scripts/eval_v2_accuracy.py)
- 评估集生成器：[scripts/build_heuristic_eval_set.py](../../specs/023-tech-classification-rebuild/scripts/build_heuristic_eval_set.py)（启发式 lower bound）
- 评估集：`data/eval/tech_classification_v2_eval.csv`（当前 100 条启发式；阶段二改为人工标注 ≥ 100 条覆盖 35 个 distinct action / 56 个四元组）
- 指标：
  - **top-1 action accuracy**：预测 action 与人工标注完全一致的比例（**SC-002 目标 ≥ 85%**）
  - **L3 accuracy**：手部·技术大类粒度一致率
  - **L1 accuracy**：握拍方式粒度一致率（横拍/直拍）
- 混淆矩阵：仅记录前 10 条最常错配对

## 3. 当前指标（启发式 lower bound — 2026-05-31）

> **本节为启发式 lower bound 而非最终基线**。受限于本阶段未完成人工标注（见 §5），
> 评估集采用「文件名强信号关键词 → expected 标签」的启发式方式生成（脚本：
> [`scripts/build_heuristic_eval_set.py`](../../specs/023-tech-classification-rebuild/scripts/build_heuristic_eval_set.py)），
> 100 条样本，覆盖 10 个 action / 5 个高频 L3 桶（字典实际共 9 桶）。**真实准确率预计高于本表数值**，
> 详见 [`specs/023-tech-classification-rebuild/eval_results.md`](../../specs/023-tech-classification-rebuild/eval_results.md) 的标签噪声分析。

| 指标 | 数值 | 备注 |
|------|------|------|
| top-1 action accuracy | **65%** | lower bound；含 ~14% 评估集标签噪声（拧↔前冲弧圈互相覆盖等） |
| L3 accuracy | **79%** | |
| L1 accuracy | **99%** | 横拍/直拍判定基本完全正确 |
| SC-002 通过 (≥ 85%) | ❌ 未达标（lower bound） | 真实值预计 ≥ 85%，待人工标注后复测 |

**评估集**：`data/eval/tech_classification_v2_eval.csv`（启发式生成，hash 见 JSON 报告）
**JSON 报告**：[`/tmp/eval_v2_report.json`](file:///tmp/eval_v2_report.json)（运行后产物，未入库）
**评估命令**：

```bash
/opt/conda/envs/coaching/bin/python3.11 \
  specs/023-tech-classification-rebuild/scripts/eval_v2_accuracy.py \
  --output /tmp/eval_v2_report.json
```

### 主要混淆模式（前 5 类，原始计数）

| 期望 → 预测 | 数量 | 性质 |
|---|---|---|
| 拧 → 前冲弧圈球 | 14 | 启发式 / 字典关键词重叠（文件名同含「拉球」「拧拉」） |
| 拨 → 挡 | 5 | 字典近义动作（推拨/挡球） |
| 握拍站位 → 拨 | 3 | 文件名「反手攻球…站位」中"反手攻球"主导命中 |
| 教学概述 → 高吊弧圈球 | 2 | 「发力传递」是高吊核心要点，启发式打偏 |
| 教学概述 → 弹 | 1 | 「反手弹击 训练计划」 |

> 上述错配中，约 23/35 经人工目检后判定为**启发式标注偏激进**，分类器输出更贴近视频实际内容。

## 4. 已知约束

- 当前 `_match_rules` 对 keyword 命中后跨手部歧义场景会 fall through 到 LLM；
  评估集中应包含至少 5 条「同 action 两手部」样本验证消歧路径
- 无 LLM 客户端时，所有未命中 keyword 的样本会归 `unclassified`；评估时应启用真实 LlmClient
- `confidence < 0.5` 强制降级 `unclassified`，会显著影响整体 accuracy；
  若希望评估「LLM 原始能力」需在 V2 实现内 patch threshold

## 5. 后续计划

- [x] **T071 阶段一**：启发式 lower bound 评估集 100 条 + 跑通 `eval_v2_accuracy.py`（2026-05-31）
- [ ] **T071 阶段二**：编制 ≥ 100 条**人工标注**样本（覆盖 35 个 distinct action / 56 个四元组），消除标签噪声
- [ ] 阶段二完成后跑 `eval_v2_accuracy.py` → 写回 §3「最终基线」副表
- [ ] 若阶段二 top-1 < 85%：增补 `tech_classification_rules.json` keyword / 调整 LLM prompt
- [ ] 周期性重测：每次新增 action 字典条目或 keyword 规则后必须重跑

### 已识别的真错配（阶段二需重点验证的分类器问题）

基于阶段一目检（详见 [`specs/023-tech-classification-rebuild/eval_results.md`](../../specs/023-tech-classification-rebuild/eval_results.md)）：

- 「拨 → unclassified」（1 例）— 单纯关键词缺失；可在 `tech_classification_rules.json` 的 `backhand_attack` 桶补充「推拨」别名
- 「拨 ↔ 挡」边界（5 例）— 字典里两动作非常近义，需阶段二人工裁判定 ground truth
