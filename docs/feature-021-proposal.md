# Feature-021（建议）：技术分类升级 & KB 提取标准化 方案

**日期**：2026-05-18  
**状态**：方案待确认

---

## 背景

1. **技术分类不完整**：当前使用的 `tech_classification_rules.json` 是扁平 21 类关键词规则，粒度粗、无层级结构。
   - 新文件 `tech_classification_rules.json_new` 提供层级化分类：握拍法 → 胶皮 → 手位 → 技术类别 → 具体动作，带 `aliases` 映射表。
2. **教练视频需重新映射**：所有 COS 中的教练视频（1015+ 个）需要用新分类体系重新分类。
3. **KB 提取标准不统一**：不同教练风格差异大（孙浩泓"知行合一"、小孙"体系化"、张继科"大师课"等），导致 LLM 提取的技术要点格式不一致，需要构建统一的提取标准和术语归一化。

---

## 新旧分类对比

### 旧版（tech_classification_rules.json）
```
21 类扁平结构，例：
  "forehand_topspin_backspin": ["正手拉下旋", "正手下旋拉球"]
  "forehand_topspin": ["正手拉球", "正手上旋拉球", "正手弧圈"]
```

### 新版（tech_classification_rules.json_new）
```
层级结构：
  shakehand (横拍)
    └─ inverted (反胶)
         ├─ forehand (正手)
         │    ├─ serve (发球): 平击发球, 奔球, 左侧下/上旋, 右侧上/下旋, 下旋转球, 不转球
         │    ├─ attack (进攻): 快攻, 突击, 扣杀, 挑, 高吊弧圈球, 前冲弧圈球, 反拉, 反冲
         │    └─ defense (防御): 挡, 快带, 兜, 放高球, 削球, 搓球
         └─ backhand (反手)
              ├─ serve (发球): ...
              ├─ attack (进攻): 拨, 弹, 扣杀, 高吊弧圈球, 前冲弧圈球, 反冲, 拧, 反拉
              └─ defense (防御): 挡, 快撕, 贴, 放高球, 兜, 削球, 搓球

+ aliases 映射（旧关键词 → 新路径）：
  "正手拉下旋" → shakehand.inverted.forehand.attack, action=高吊弧圈球
  "正手拉球"   → shakehand.inverted.forehand.attack, action=前冲弧圈球
  ...
```

新分类的优势：
- **层级明确**：握拍法/胶皮/手位/类别/动作 五层结构
- **动作粒度**：区分"高吊弧圈球"和"前冲弧圈球"（旧版混在 forehand_topspin）
- **可扩展**：后续可加 penhold（直拍）、pips（颗粒胶）等分支
- **向后兼容**：aliases 映射保证旧关键词可自动转换

---

## 方案设计

### Phase A：分类器升级（TechClassifierV2）

**文件**：`src/services/tech_classifier.py`

1. 新增 `TechClassifierV2` 类，加载 `tech_classification_rules.json_new`
2. 匹配流程：
   - Step 1: 在 `aliases` 中查旧关键词 → 返回层级路径 + action
   - Step 2: 在层级树中逐层匹配关键词（动作名优先，类别名其次）
   - Step 3: LLM fallback（Prompt 适配新分类体系）
3. 输出：`ClassificationResult` 新增字段：
   - `tech_path`: 如 `shakehand.inverted.forehand.attack.高吊弧圈球`
   - `action`: 如 `高吊弧圈球`
   - `grip_type`: `shakehand` | `penhold` | `unknown`
   - `rubber_type`: `inverted` | `pips` | `unknown`
   - `hand`: `forehand` | `backhand` | `both`
   - `category`: `serve` | `attack` | `defense`
   - 保留 `tech_category`（旧 21 类 ID，通过映射表自动填充）

### Phase B：DB 迁移

**Alembic 迁移**（0020）：
- `coach_video_classifications` 表新增列：
  - `tech_path VARCHAR(256)` — 完整层级路径
  - `action VARCHAR(64)` — 具体动作名
  - `grip_type VARCHAR(32)` — 握拍法
  - `rubber_type VARCHAR(32)` — 胶皮类型
  - `hand VARCHAR(16)` — 正手/反手
  - `category VARCHAR(32)` — 技术类别（serve/attack/defense）
- `video_classifications` 表（Feature-004 yaml 规则）暂不升级（取决于决策）

### Phase C：全量重新扫描 + API 适配

1. 用 `TechClassifierV2` 重新扫描 COS 全量视频
2. 更新 `POST /classifications/scan` API，支持 `classifier_version=v2` 参数
3. 更新 `GET /classifications` API，返回新增字段
4. 更新 `ClassificationGateService`：`kb_extraction` 门槛校验适配新分类

### Phase D：KB 提取标准化

**文件**：`src/services/kb_extraction_pipeline/step_executors/audio_kb_extract.py`、`visual_kb_extract.py`

1. **统一 LLM Prompt Schema**：

```json
{
  "action": "高吊弧圈球",
  "tech_path": "shakehand.inverted.forehand.attack.高吊弧圈球",
  "key_points": [
    {
      "phase": "准备|引拍|击球|随挥|还原",
      "instruction": "该阶段的技术指导",
      "body_part": "手臂|手腕|腰部|腿部|重心",
      "cue_words": ["教练原话关键词"]
    }
  ],
  "common_errors": ["常见错误"],
  "drill_suggestions": ["练习建议"],
  "confidence": 0.85
}
```

2. **ExpertTechPoint 模型新增 `action` 字段**（DB 迁移）

### Phase E：标准构建按 action 聚合

**文件**：`src/services/tech_standard_builder.py`

- 当前按 21 类 `tech_category` 聚合 → 改为按 `action`（~20 个具体动作）聚合
- 这样"高吊弧圈球"和"前冲弧圈球"各自有独立的标准参数范围
- 更精准的偏差诊断

### Phase F：术语归一化层

**新文件**：`src/services/terminology_normalizer.py`

- 将教练口语化表达映射到标准术语：
  - "包住球" → "摩擦加厚"
  - "亮板" → "拍面打开"
  - "收小臂" → "前臂内收"
- 实现：`aliases` 反向映射 + LLM 归一化 Prompt
- 在 `audio_kb_extract` 后、写入 KB 前执行

---

## 实施路线建议

| 阶段 | 预估工作量 | 核心产出 |
|------|-----------|----------|
| Phase A | 2-3 天 | `TechClassifierV2` + 单元测试 |
| Phase B | 1 天 | Alembic 迁移 + 模型更新 |
| Phase C | 1-2 天 | 全量扫描 + API 适配 + 回归测试 |
| Phase D | 2-3 天 | 标准化 Prompt + ExpertTechPoint 迁移 |
| Phase E | 1-2 天 | 标准构建器改造 + API 适配 |
| Phase F | 1-2 天 | 术语归一化器 + 测试 |
| **合计** | **8-13 天** | Feature-021 |

---

## 待确认事项

1. **推进节奏**：一次性全部做完，还是分阶段（先 Phase A-C，再 D-F）？
2. **旧分类兼容**：`video_classifications` 表（yaml 规则）是否也升级？
3. **action 粒度**：当前 ~20 个动作是否需要进一步细化（如区分"反手拧"的正手版/反手版）？
4. **Feature-017~020 状态**：这些 spec 是否已经在进行中？需确认优先级。