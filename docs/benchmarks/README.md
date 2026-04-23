# 精准度基准数据集

本目录存储用于验证分析精准度的人工标注数据集，对应规范中的 SC-001 和 SC-002。

## 数据集说明

### expert_annotation_v1.json

用于验证 **SC-001（专家视频技术维度覆盖率 ≥90%）**。

**格式**:
```json
{
  "version": "1.0",
  "description": "...",
  "annotations": [
    {
      "video_id": "seg_001",
      "expected_dimensions": ["elbow_angle", "swing_trajectory", "contact_timing", "weight_transfer"],
      "action_type": "forehand_topspin",
      "notes": ""
    }
  ]
}
```

**字段说明**:
- `video_id`: 视频片段唯一标识（与测试夹具对应）
- `expected_dimensions`: 该片段应提取到的维度列表
- `action_type`: 动作类型（`forehand_topspin` 或 `backhand_push`）
- `notes`: 标注说明

### deviation_annotation_v1.json

用于验证 **SC-002（运动员偏差分析一致率 ≥85%）**。

**格式**:
```json
{
  "version": "1.0",
  "description": "...",
  "annotations": [
    {
      "video_id": "seg_001",
      "action_type": "forehand_topspin",
      "expected_deviations": [
        {
          "dimension": "elbow_angle",
          "direction": "above",
          "notes": ""
        }
      ]
    }
  ]
}
```

**字段说明**:
- `video_id`: 视频片段标识
- `action_type`: 动作类型
- `expected_deviations`: 期望检测到的偏差列表
  - `dimension`: 偏差维度
  - `direction`: `above`（偏高）/ `below`（偏低）/ `none`（无偏差）
  - `notes`: 标注说明

## 版本管理

- 数据集文件通过 **Git LFS** 管理（视频文件）
- JSON 标注文件直接提交到版本库
- 每次更新需递增版本号（`v1` → `v2`）并在此 README 中记录变更

## 使用方法

基准测试通过 `tests/benchmarks/test_accuracy_benchmarks.py` 运行：

```bash
pytest tests/benchmarks/ -m benchmark -v
```

测试失败（覆盖率 < 90% 或一致率 < 85%）时会阻止 CI 合并。

## 数据集状态

| 数据集 | 版本 | 片段数 | 标注人 | 日期 |
|--------|------|--------|--------|------|
| expert_annotation_v1.json | v1 | 5 | placeholder | 2026-04-19 |
| deviation_annotation_v1.json | v1 | 5 | placeholder | 2026-04-19 |
