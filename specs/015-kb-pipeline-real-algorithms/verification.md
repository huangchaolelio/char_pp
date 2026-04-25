# Feature-015 Verification — 真实算法接入

**Status**: scaffold（CI 已通过；部署验证 TODO）
**Last updated**: 2026-04-25

本文件追踪 Feature-015 的 Success Criteria 验证状态。CI 可自动验证的项目（SC-001 视觉部分、SC-003 结构、SC-004 错误码）在 PR 阶段就能给绿灯；需要真实教练视频/真实 Whisper/真实 LLM 的项目（SC-001 音频部分、SC-002、SC-005、SC-006）必须在部署环境运行 `scripts/run_reference_regression.py` 补齐。

---

## Success Criteria 矩阵

| # | 标准 | 自动化方式 | 状态 | 实测值 / 证据 | 备注 |
|---|------|-----------|------|--------------|------|
| SC-001 (visual) | 视觉路 ≥2 条目 | `tests/integration/test_visual_kb_real.py` | ✓ PASS | 合成 pose.json → visual_kb_extract 产出 ≥1 条目，backend≠scaffold | CI 每次 PR 跑 |
| SC-001 (audio)  | 音频路 ≥1 条目 | `tests/integration/test_audio_kb_real.py` | ✓ PASS | mocked TranscriptTechParser → 1 条 `source_type='audio'` 含 `raw_text_span` | 真实讲解视频验证走 US3 回归 |
| SC-002 | 10 分钟视频 ≤ F-002 ×90% | `run_reference_regression.py --measure-wallclock` | ☐ TODO | — | 需要部署环境 + manifest 填入 `baseline_f002_seconds`；脚本会计算比值并标 PASS/FAIL |
| SC-003 | 参考视频条目数 ∈ 预定义范围 | `run_reference_regression.py`（manifest 模式） | ☐ TODO | — | 结构已就位，CI 用 mock HTTP 验证（`test_real_algorithms_regression.py` 3 路径全绿） |
| SC-004 | 100% 失败返回结构化错误 | `tests/unit/test_error_codes.py` + `tests/unit/test_video_quality_gate.py` + `tests/unit/test_audio_kb_llm_gate.py` | ✓ PASS | 9 个错误码常量全导出；`VIDEO_QUALITY_REJECTED:` / `LLM_UNCONFIGURED:` 前缀在 executor 层可复现 | 部署阶段继续观察 `pipeline_steps.error_message` 分布 |
| SC-005 | 视觉路批次成功率 ≥95% | 部署回归脚本（N=10） | ☐ TODO | — | 运维跑 `--random-sample 10` + 复核 `pose_analysis.output_summary.keypoints_frame_count > 0` 的比例 |
| SC-006 | 音频路批次成功率 ≥85% | 部署回归脚本（有讲解子集） | ☐ TODO | — | 用 manifest 标 `has_speech=true` 的子集做分母，`kb_items_count ≥ 1` 做分子 |

---

## CI 测试统计（Feature-015 新增）

| 测试文件 | 用例数 | 说明 |
|---------|--------|------|
| `tests/unit/test_artifact_parsers.py` | 12 | pose.json / transcript.json 读写往返 + 容错（FR-002 / FR-007 / Q4）|
| `tests/unit/test_error_codes.py` | 7 | 9 个错误码常量 + `format_error()` 合约（FR-016）|
| `tests/unit/test_video_quality_gate.py` | 3 | `VideoQualityRejected → VIDEO_QUALITY_REJECTED:` 前缀翻译（FR-006）|
| `tests/unit/test_audio_kb_llm_gate.py` | 1 | Venus/OpenAI 均未配置 → `LLM_UNCONFIGURED:` fail fast（FR-011）|
| `tests/integration/test_visual_kb_real.py` | 2 | 合成 pose → visual_kb_extract 产出 + 空 frames 降级（SC-001 视觉）|
| `tests/integration/test_audio_kb_real.py` | 2 | 合成 transcript → kb_items(audio) + 上游 skipped 传播（FR-009/FR-010/FR-012）|
| `tests/integration/test_real_algorithms_regression.py` | 7 | MockTransport 驱动回归脚本 happy path / 越界 / 失败作业 / MD 渲染 / CLI 退出码 |
| **合计** | **34** | |

---

## 部署阶段 TODO

1. **填写 manifest**：编辑 `specs/015-kb-pipeline-real-algorithms/reference_videos.json`，把 3 条占位记录的 `cos_object_key` 换成真实 COS 路径，并为运维有信心的视频填入 `baseline_f002_seconds`（Feature-002 旧流程的历史耗时）。
2. **运行 US3 回归（SC-003）**：
   ```bash
   /opt/conda/envs/coaching/bin/python3.11 \
     specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py \
     --manifest specs/015-kb-pipeline-real-algorithms/reference_videos.json \
     --output specs/015-kb-pipeline-real-algorithms/verification.md
   ```
   退出码 0 = SC-003 达标；1 = 至少一条条目数越界。脚本会把 Markdown 表格追加/覆盖写回本文件。
3. **运行 US4 耗时验证（SC-002）**：追加 `--measure-wallclock`。报告新增 `Baseline(s)` + `Ratio vs Baseline` 两列，比值 ≤0.9 = PASS。
4. **运行批次抽样（SC-005 + SC-006）**：
   ```bash
   /opt/conda/envs/coaching/bin/python3.11 \
     specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py \
     --random-sample 10 \
     --output /tmp/f015_batch_verification.md
   ```
   抽 10 个已分类视频，人工核查：
   - SC-005：`pose_analysis.output_summary.keypoints_frame_count > 0` 的比例 ≥ 9/10
   - SC-006：`audio_kb_extract.output_summary.kb_items_count ≥ 1` 的比例（分母=有讲解视频数）≥ 85%
5. **填回本文件**：把回归脚本产出的 Markdown 表格粘进"Success Criteria 矩阵"下方，把 `☐ TODO` 改成 `✓ PASS` / `✗ FAIL`。

---

## 已知局限 & 非目标

- **LLM JSON 偶发不稳定**：spec 已接受 15% 宽容度（SC-006 = 85% 而非 100%）。若连续多次 `LLM_JSON_PARSE` 报警，应审视 prompt 而非放宽阈值。
- **算法精度基准**：Feature-015 不引入新模型、不调整阈值，精度基准沿用 Feature-002。`docs/benchmarks/` 若有历史基准数据，SC-003 的 `expected_items_min/max` 应与之对齐。
- **F-002 旧流程不可复跑**：Feature-013 已把 `KbExtractionService` 改为 stub，因此 SC-002 只能通过 manifest 中手动填入的历史耗时基线比对，无法做 A/B。

---

## 变更记录

| 日期 | 变更 |
|------|------|
| 2026-04-25 | 初始版本：CI 自动化部分就位，部署部分列 TODO |
