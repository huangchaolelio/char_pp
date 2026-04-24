---
alwaysApply: true
---

# 21 类技术分类（TECH_CATEGORIES）

定义位置：`src/services/tech_classifier.py::TECH_CATEGORIES`

```
forehand_push_long       forehand_attack          forehand_topspin
forehand_topspin_backspin forehand_loop_fast      forehand_loop_high
forehand_flick           backhand_attack          backhand_topspin
backhand_topspin_backspin backhand_flick          backhand_push
serve                    receive                   footwork
forehand_backhand_transition  defense             penhold_reverse
stance_posture           general                   unclassified
```

- 枚举值禁止在代码中用字符串字面量替代，必须引用 `TECH_CATEGORIES` 枚举

# 分类规则配置

- 规则文件：`config/tech_classification_rules.json`
- **顺序敏感**：精细类（如 `forehand_topspin_backspin`）必须在通用类（如 `forehand_topspin`）之前
- 分类置信度 < 0.5 时归入 `unclassified`
- LLM 兜底策略：Venus Proxy 优先 → OpenAI fallback

# 视频分类 yaml 配置

- 文件：`src/config/video_classification.yaml`
- 含 12 位教练的 `cos_prefix_keywords` 关键词匹配规则
- `_infer_coach()` 遍历配置，首个命中返回；修改配置后需重启服务

# 知识库提取工作流

1. 查询待处理：`GET /api/v1/classifications?tech_category=X&kb_extracted=false`
2. 提交任务：`POST /api/v1/tasks`（type=expert）
3. 完成后标记：`PATCH /api/v1/classifications/{id}` 设 `kb_extracted=true`
