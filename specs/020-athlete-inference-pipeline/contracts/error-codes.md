# 错误码登记 · Feature-020 运动员推理流水线

**权威源**：`src/api/errors.py`（章程原则 IX 错误码集中化）。本 feature 新增 5 个 `ErrorCode`，全部**只增不改**，HTTP 状态与默认消息随登记一并落 3 张表（`ErrorCode` 枚举 / `ERROR_STATUS_MAP` / `ERROR_DEFAULT_MESSAGE`）。

## 新增错误码清单

| ErrorCode | HTTP | 默认消息 | 触发层 | `details` 字段 |
|-----------|------|---------|--------|---------------|
| `ATHLETE_ROOT_UNREADABLE` | 502 | 运动员视频根路径不可读或 COS 凭证无效 | `CosAthleteScanner._list_all_mp4s` 捕获 `CosServiceError` 后封装 | `{ "root_prefix": <COS_VIDEO_ALL_ATHLETE>, "upstream_error_code": ... }` |
| `ATHLETE_DIRECTORY_MAP_MISSING` | 500 | 运动员目录映射配置文件缺失 | `CosAthleteScanner.from_settings()` 在 `config/athlete_directory_map.json` 不存在时 fail-fast | `{ "expected_path": ".../config/athlete_directory_map.json" }` |
| `ATHLETE_VIDEO_NOT_PREPROCESSED` | 409 | 运动员视频尚未完成预处理，不能直接诊断 | `AthleteSubmissionService.submit_diagnosis` 预校验 `preprocessed=true` 失败 | `{ "athlete_video_classification_id": ..., "cos_object_key": ... }` |
| `STANDARD_NOT_AVAILABLE` | 409 | 该技术类别暂无可用的激活版标准 | `DiagnosisService` 查不到 `tech_standards(tech_category=..., status='active')` | `{ "tech_category": ..., "hint": "请在 KB 管理页发布对应类别的 published 标准" }` |
| `ATHLETE_VIDEO_POSE_UNUSABLE` | 422 | 运动员视频姿态提取全程无可用关键点 | `pose_estimator` 全帧空骨架时 `DiagnosisService` 抛 | `{ "athlete_video_classification_id": ..., "segment_count": ... }` |
| `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND` | 404 | 运动员视频素材记录不存在 | 预处理 / 诊断提交时传入的 `athlete_video_classification_id` 在 `athlete_video_classifications` 表查无此行 | `{ "resource_id": <uuid> }` |

## 与现有错误码的互斥关系

| 现有 code | 本 feature 如何区分 |
|----------|-------------------|
| `NO_ACTIVE_KB_FOR_CATEGORY` (F-019) | 指 **KB 草稿未激活**；本 feature 的 `STANDARD_NOT_AVAILABLE` 指 **standards 未构建**——两者位于 STANDARDIZATION 阶段的不同子步骤，不可混用（研究决策 R9） |
| `COS_UPSTREAM_FAILED` | 泛指任意 COS 调用失败；本 feature `ATHLETE_ROOT_UNREADABLE` 仅针对"运动员根路径"这一个特定语义，便于运维 grep runbook |
| `PREPROCESSING_JOB_NOT_FOUND` (F-016) | 查 preprocessing job 本身不存在；本 feature `ATHLETE_VIDEO_NOT_PREPROCESSED` 指运动员素材这一条从未被预处理过 |
| `VIDEO_QUALITY_REJECTED` (F-016) | 预处理阶段 ffprobe 判定帧率/分辨率不过关；本 feature `ATHLETE_VIDEO_POSE_UNUSABLE` 指**已通过预处理**但姿态估计层失败 |

## 错误信封示例

### `ATHLETE_VIDEO_NOT_PREPROCESSED`
```json
{
  "success": false,
  "error": {
    "code": "ATHLETE_VIDEO_NOT_PREPROCESSED",
    "message": "运动员视频尚未完成预处理，不能直接诊断",
    "details": {
      "athlete_video_classification_id": "7e5e3f7a-...-...",
      "cos_object_key": "charhuang/tt_video/athletes/张三/正手攻球01.mp4"
    }
  }
}
```

### `STANDARD_NOT_AVAILABLE`
```json
{
  "success": false,
  "error": {
    "code": "STANDARD_NOT_AVAILABLE",
    "message": "该技术类别暂无可用的激活版标准",
    "details": {
      "tech_category": "forehand_attack",
      "hint": "请在 KB 管理页发布对应类别的 published 标准"
    }
  }
}
```

## CI 守卫

- `scripts/audit/workflow_drift.py` 会扫描 `src/api/errors.py` 与 `docs/business-workflow.md § 7.4` 一致性——本 feature 已在 `docs/business-workflow.md § 7.4` 同步登记，CI 通过
- 合约测试 `tests/contract/test_submit_athlete_diagnosis.py` 等用例将覆盖"触发场景 → 映射到对应 ErrorCode 与 HTTP 状态"的最小断言集
