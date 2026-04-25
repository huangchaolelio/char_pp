# Quickstart: Feature-015 真实算法接入

**功能**: `015-kb-pipeline-real-algorithms` | **日期**: 2026-04-25

## 目标

端到端验证 Feature-015 的 4 个用户故事。前两步（US1/US2）可在本地 CI 运行（合成 artifact fixture）；后两步（US3/US4）需要**部署环境**+**参考视频集 fixture**。

**前提条件**：
- Feature-014 的 Alembic 0013 已应用
- 4 个 Celery Worker 正常运行（特别是 `kb_extraction` concurrency=2）
- `.env` 含 `VENUS_TOKEN` 或 `OPENAI_API_KEY`（US2 必需；US1 无关）
- `coach_video_classifications` 有非 unclassified 的测试数据

---

## Step 1: 运行 Feature-015 单元 + 集成测试（CI 级）

```bash
cd /data/charhuang/char_ai_coding/charhuang_pp_cn

# 单元测试（视频质量预检、artifact 容错解析）
/opt/conda/envs/coaching/bin/python3.11 -m pytest \
  tests/unit/test_video_quality_gate.py \
  tests/unit/test_artifact_parsers.py -v

# 集成测试（合成 artifact fixture 驱动，不需要真实 Whisper / LLM / 视频）
/opt/conda/envs/coaching/bin/python3.11 -m pytest \
  tests/integration/test_visual_kb_real.py \
  tests/integration/test_audio_kb_real.py -v
```

**预期**：全部通过。这些测试用**合成的 pose.json 和 transcript.json** 作为上游 artifact，验证 `visual_kb_extract` / `audio_kb_extract` 的接线正确性（不依赖真实视频或模型）。

---

## Step 2: 准备参考视频集（US3 前置）

1. 获取 3–5 个典型教练视频（覆盖 forehand/backhand/serve 等技术类别）
2. 上传到 COS 的 `fixtures/f015/` 前缀下
3. 在 PostgreSQL 里为这些视频插入 `coach_video_classifications` 行（`classification_source='manual'`, `kb_extracted=false`）
4. 填写 `specs/015-kb-pipeline-real-algorithms/reference_videos.json`：

```json
{
  "videos": [
    {
      "name": "forehand_topspin_sample_1",
      "cos_object_key": "fixtures/f015/forehand_topspin_1.mp4",
      "tech_category": "forehand_topspin",
      "expected_items_min": 3,
      "expected_items_max": 25,
      "has_speech": true,
      "notes": "清晰讲解，30fps 1080p，15 秒"
    }
  ]
}
```

---

## Step 3: 运行参考视频集回归（US3 + SC-003）

```bash
/opt/conda/envs/coaching/bin/python3.11 \
  specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py \
  --manifest specs/015-kb-pipeline-real-algorithms/reference_videos.json \
  --output specs/015-kb-pipeline-real-algorithms/verification.md
```

脚本行为：
1. 加载 manifest
2. 对每个视频提交 `POST /tasks/kb-extraction`
3. 轮询 `GET /extraction-jobs/{id}` 直到 success 或 failed
4. 读 `expert_tech_points` 统计每个视频的 visual/audio/visual+audio 条目数
5. 断言条目数 ∈ [expected_items_min, expected_items_max]
6. 汇总结果到 verification.md

**验证**：
- 每个视频的 `expert_tech_points` 行数 ∈ 预期范围（SC-003）
- 有讲解的视频至少有 1 条 audio / visual+audio 来源的条目
- `kb_conflicts` 表的 `superseded_by_job_id IS NULL AND resolved_at IS NULL` 记录合理（冲突项不进主 KB）

---

## Step 4: 批次成功率验证（US1/US2 + SC-005/SC-006）

准备 10 个随机教练视频（从 `coach_video_classifications` 表抽样）：

```bash
/opt/conda/envs/coaching/bin/python3.11 \
  specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py \
  --random-sample 10 \
  --output /tmp/f015_batch_verification.md
```

**验证**：
- **SC-005**（视觉路成功率）：10 个视频中至少 9 个的 `pose_analysis` step 最终 status=success 且 `keypoints_frame_count >= 30`（排除 `VIDEO_QUALITY_REJECTED` 的视频不计入分母）
- **SC-006**（音频路 LLM 成功率）：有讲解音频的子集（`has_speech=true` 的视频）里，至少 85% 产出 `audio_kb_extract.output_summary.kb_items_count >= 1`

---

## Step 5: 10 分钟视频耗时对比（US4 + SC-002）

```bash
# 选一段 ~10 分钟教练视频
TARGET_VIDEO="fixtures/f015/forehand_topspin_10min.mp4"

# 提交
JOB_RESP=$(curl -sS -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
    -H 'Content-Type: application/json' \
    -d "{\"cos_object_key\": \"$TARGET_VIDEO\", \"enable_audio_analysis\": true}")
TASK_ID=$(echo "$JOB_RESP" | jq -r .items[0].task_id)

# 拉 job_id
JOB_ID=$(curl -sS "http://localhost:8080/api/v1/extraction-jobs?page=1&page_size=50" \
    | jq -r ".items[] | select(.analysis_task_id==\"$TASK_ID\") | .job_id")

# 轮询 + 记录总耗时
START_TS=$(date +%s)
while true; do
    STATUS=$(curl -sS "http://localhost:8080/api/v1/extraction-jobs/$JOB_ID" | jq -r .status)
    if [[ "$STATUS" == "success" || "$STATUS" == "failed" ]]; then
        END_TS=$(date +%s)
        break
    fi
    sleep 10
done

ELAPSED=$((END_TS - START_TS))
echo "Feature-015 total elapsed: ${ELAPSED}s"

# 与 Feature-002 基线对比（基线需预先记录）
# SC-002: ELAPSED ≤ 0.9 × baseline_f002_seconds
```

**验证**：
- 总耗时 ≤ Feature-002 基线 × 0.9（SC-002）
- 所有 step 最终 success（或 audio 路 skipped）

---

## Step 6: 错误码验证（FR-016）

### 视频质量不达标

```bash
# 用一段低帧率或低分辨率视频提交
LOW_QUALITY="fixtures/f015/low_fps_10fps.mp4"
curl -sS -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
    -d "{\"cos_object_key\": \"$LOW_QUALITY\"}" -H 'Content-Type: application/json'

# 等作业终态 failed
# 查 pose_analysis error_message
curl -sS "http://localhost:8080/api/v1/extraction-jobs/$JOB_ID" \
    | jq '.steps[] | select(.step_type=="pose_analysis") | .error_message'
# 预期: "VIDEO_QUALITY_REJECTED: fps=10 vs 15"
```

### LLM 未配置

```bash
# 临时卸掉 LLM 环境变量
env -u VENUS_TOKEN -u OPENAI_API_KEY \
    setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
    --loglevel=info --concurrency=2 -Q kb_extraction -n test_no_llm@%h >> /tmp/celery_no_llm.log 2>&1 &

# 提交一个音频路会走的视频
# 查 audio_kb_extract error_message
# 预期: "LLM_UNCONFIGURED: no VENUS_TOKEN and no OPENAI_API_KEY"
```

---

## Step 7: 回归确认 Feature-014 未被破坏

```bash
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ --tb=no -q
```

**门控**：498 passed（Feature-014 基线）+ 新增 Feature-015 测试全部通过，全仓 0 failed。

---

## 故障排查

| 症状 | 可能原因 | 修复 |
|------|---------|------|
| `pose_analysis` 一直 failed 且错误为 `POSE_NO_KEYPOINTS:` | 视频里没有检测到人物 | 检查视频内容是否真有挥拍动作 |
| `audio_kb_extract` 返回空 kb_items | LLM 认为讲解内容不含技术参数 | 查看 transcript.json 内容是否真的有技术描述 |
| `kb_conflicts` 表堆积很多条目 | 视觉/音频差异频繁 >10% | 检查视频讲解的准确性；阈值调整超出本 Feature 范围 |
| US4 耗时远超 90% 基线 | 单 Worker 并行不够 / LLM 响应慢 | 确认 kb_extraction Worker concurrency=2；检查 Venus/OpenAI 网络 |
