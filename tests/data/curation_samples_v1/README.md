# Curation Benchmark Sample Set v1

`manifest.jsonl` — 每行一个 JSON 样本，对应一条 COS 视频的人工标注分段集合。
**视频本体不在仓库中**——manifest 只存指针（cos_object_key）+ 人工标注；
真实视频读取在 staging 环境通过 COS SDK 获得。

## 用途

供 `scripts/run_curation_benchmark.py` 加载并跑算法，对照 spec
SC-001 / SC-002 计算 precision / recall / token reduction，输出 baseline JSON。

## 格式（每行一个 JSON 对象）

```jsonc
{
  "sample_id": "S001",                        // 唯一 ID（标注时分配）
  "cos_object_key": "charhuang/x/y.mp4",      // COS 对象键
  "tech_category": "forehand_topspin",         // 与 coach_video_classifications 一致的 21 类
  "coach_name": "张继科",                      // 目标教练（可空，未识别时填 null）
  "annotated_at": "2026-05-19",                // 人工标注日期
  "annotator": "ops_alice",                    // 标注员 ID
  "segments": [
    {
      "segment_index": 0,                      // 与 video_preprocessing_segments.segment_index 对齐
      "start_ms": 0,                           // 段起点（毫秒）
      "end_ms": 60000,                         // 段终点（毫秒）
      "transcript_text": "...",                // Whisper 转录文本（脱敏后入库）
      "human_label": "accepted"                // accepted | rejected | uncertain
    }
    // ... more segments
  ]
}
```

## 当前状态

仓库中携带 **3 条 synthetic 样本**（`example_synthetic.jsonl`）专门给
benchmark 脚本做端到端 smoke test 用——这些样本的 transcript 来自合成短文本，
不代表真实视频，**不可作为 SC 达标的依据**。

真实 ≥ 30 条样本由运营按以下流程构建（不在本 PR 范围）：

1. 从 `coach_video_classifications` 选 ≥ 30 条覆盖各类技术 + 多教练的视频
2. 跑 `scrip ts/run_curation_benchmark.py` 之前先跑一次清洗任务，生成
   逐分段 transcript 落到 `video_curation_segment_results.dim_breakdown`
   或 audio_transcripts；运营在标注 UI（待建）按段标 accepted/rejected/uncertain
3. 导出标注结果合并到 `manifest.jsonl`，提交到 staging-only 卷
   （或仓库中按段大小判断；transcript 内容含教练个人言语数据，
   不要直接入 git history）

## 跑 baseline

```bash
# 用合成样本跑 smoke：
/opt/conda/envs/coaching/bin/python3.11 scripts/run_curation_benchmark.py \
    --manifest tests/data/curation_samples_v1/example_synthetic.jsonl \
    --output specs/021-video-content-curation/benchmark/v1_synthetic.json \
    --no-llm

# 用真实标注集（在 staging 环境）：
python3 scripts/run_curation_benchmark.py \
    --manifest /data/curation_samples_v1/manifest.jsonl \
    --output specs/021-video-content-curation/benchmark/v1_baseline.json
```

退出码：

- `0` 全部 SC check 通过
- `1` 至少一项 SC 失败（CI 据此 fail）
- `2`/`3`/`4` 输入错误（manifest 缺失 / 解析失败 / 空文件）
