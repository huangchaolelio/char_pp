---
alwaysApply: true
---

# 技术分类（V2 — Feature-023 后口径，2026-05-31 起）

> Feature-023 已**物理删除**旧的 `TECH_CATEGORIES` 21 类枚举（V1）。当前生效口径
> 为 `tech_actions` 字典 56 行 V2，由迁移 `0022_tech_taxonomy_rebuild` 落库。

## 字典与四元组

- 单一事实来源：`tech_actions` 表（56 行 v2）
- 每行是一个**四元组** `(grip_style, hand_side, stroke_phase, action)`：
  - `grip_style` ∈ {`横拍`, `直拍`}
  - `hand_side` ∈ {`正手`, `反手`, `通用`}
  - `stroke_phase` ∈ {`进攻`, `防御`, `教学辅助`, `发接发`, `步法`, `转换`}
  - `action` 为 44 个动作字面量（如 `前冲弧圈球`/`拧`/`挡`/`握拍站位` 等）
- L3 桶：`(hand_side, stroke_phase)` 组合，共 5 桶（正手/反手·进攻/防御 + 通用·教学辅助）
- **代码内禁止使用字符串字面量**：分类器结果必须落在 `tech_actions` 字典；不在字典内
  的预测一律降级为 `unclassified`
- 当前阶段仅启用 `grip_style=横拍` 子集；直拍位留空待 Path 2 拓展

## 分类器（V2）

- 实现位置：`src/services/tech_classifier.py::TechClassifier`
- 调用路径：`src/workers/classification_task.py::classify_video`
- 流程：
  1. **规则层**（`config/tech_classification_rules.json`）：keyword 命中即返回（顺序敏感，精细类在通用类之前）
  2. **LLM 兜底**：Venus Proxy 优先 → OpenAI fallback；JSON mode + 字典 `enum` 块强约束
  3. **置信度阈值**：`confidence < 0.5` → 强制降级为 `unclassified`
  4. **字典强约束**：LLM 返回的四元组必须在 `tech_actions` 中存在，否则 fallback 至 `unclassified`

## 配置文件

| 文件 | 用途 | 修改后影响 |
|------|------|-----------|
| `config/tech_classification_rules.json` | 规则层 keyword → action 映射 | 立即生效（启动时加载） |
| `src/config/video_classification.yaml` | `_infer_coach()` 教练姓名匹配（与分类器无关） | 修改后需重启服务 |
| `tech_actions` 表 | 字典 56 行 V2，单一事实来源 | 必须通过迁移变更，禁止手改 |

## 知识库提取工作流（V2 字段）

1. 查询待处理：`GET /api/v1/classifications?action=<action>&kb_extracted=false`（按 action 过滤；旧 `tech_category` 参数已下线）
2. 提交任务：`POST /api/v1/tasks`（type=expert）
3. 完成后标记：`PATCH /api/v1/classifications/{id}` 设 `kb_extracted=true`

## 准确率基线

- 文档：`docs/benchmarks/tech_classification_v2.md`
- 评估脚本：`specs/023-tech-classification-rebuild/scripts/eval_v2_accuracy.py`
- 评估集生成器：`specs/023-tech-classification-rebuild/scripts/build_heuristic_eval_set.py`（启发式 lower bound）
- 当前指标（启发式 lower bound，2026-05-31）：top-1=65% / L3=79% / L1=99%
- SC-002 目标：top-1 ≥ 85%（待人工标注 ground truth 后复测）

## 已下线（v2.0.0 直接物理删除）

- 旧枚举 `TECH_CATEGORIES`（21 类）
- 旧字段 `tech_category`（API + DB 中已替换为 `action` / `action_id`）
- Feature-021 兼容方案（被 Feature-023 上位实现，参见 `docs/feature-021-proposal.md`）
