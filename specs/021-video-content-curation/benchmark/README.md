# Feature-021 基准回归

存放清洗算法 baseline JSON 与运行指引。

## 文件清单

- `v1_synthetic_baseline.json` — 用 `tests/data/curation_samples_v1/example_synthetic.jsonl`
  3 条合成样本跑出的 baseline，用作 **smoke test 参考值**（数据合成、不能代表真实
  样本上的 SC 达标）
- `v1_baseline.json`（**待 staging 环境跑**）— 用 ≥ 30 条人工标注真实样本跑出的
  正式 baseline；上线 / 规范升级前必须达标

## 跑 baseline

合成样本（仓库内零依赖，本地即可跑）：

```bash
/opt/conda/envs/coaching/bin/python3.11 scripts/run_curation_benchmark.py \
    --manifest tests/data/curation_samples_v1/example_synthetic.jsonl \
    --output specs/021-video-content-curation/benchmark/v1_synthetic_baseline.json \
    --no-llm
```

真实样本（在 staging 环境，需挂载真实标注卷）：

```bash
python3 scripts/run_curation_benchmark.py \
    --manifest /data/curation_samples_v1/manifest.jsonl \
    --rubric-version v1 \
    --output specs/021-video-content-curation/benchmark/v1_baseline.json
```

## 当前 baseline 状态

| 数据集 | precision_accepted | recall_rejected | token_reduction | SC 状态 |
|--------|-------------------:|----------------:|----------------:|---------|
| `v1_synthetic_baseline.json` | 1.000 | 1.000 | 0.398 | ✅（仅 smoke）|
| `v1_baseline.json` | — | — | — | **待 staging 跑**（运营任务）|

> 合成样本因 transcript 是按规则手工构造的，自然 100% 命中——它**只验证脚本流水线**，
> **不**验证算法精度。SC-001/002 真正达标需要 ≥ 30 条真实人工标注样本。

## SC 达标定义（来自 spec.md）

| 检查项 | 阈值 | 实测来源 |
|--------|------|---------|
| `sc_001_recall_rejected_ge_0_85` | ≥ 0.85 | 算法对"无效片段"的召回率 |
| `sc_001_precision_accepted_ge_0_85` | ≥ 0.85 | 算法对"有效片段"的精确率 |
| `sc_002_token_reduction_ge_0_30` | ≥ 0.30 | 清洗后送给 KB 抽取的 transcript 字符量 / 清洗前总字符量 |
| SC-003 术语重叠率提升 | ≥ 0.20 | **本脚本不产出**——需在 staging 跑两次完整 KB 抽取（清洗前 vs 清洗后）后比对 |

## 退出码

| 退出码 | 含义 | CI 行为 |
|--------|------|---------|
| 0 | 全部 SC check 通过 | green |
| 1 | 至少一项 SC 失败 | **fail** |
| 2/3/4 | 输入错误（manifest 缺失/解析失败/空文件） | fail |

## 何时跑

- 上线前最后一次冒烟（`Phase 8 / T088`）
- 规范升级（v1 → v2）后必须重新跑，确认新阈值不掉精度
- 算法核心改动（`decision_engine.py` 任何逻辑变更）后跑回归
- 月度计划任务（可选，监控算法漂移）

## 参考

- 脚本：`scripts/run_curation_benchmark.py`
- 样本格式：`tests/data/curation_samples_v1/README.md`
- spec 指标定义：`specs/021-video-content-curation/spec.md § 成功标准`
