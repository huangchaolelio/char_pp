# Whisper 模型登记: whisper-small (中文)

**登记时间**: 2026-04-19
**用途**: Feature-002 音频增强型教练视频知识库提取

## 模型信息

| 字段 | 值 |
|------|----|
| 模型名称 | whisper-small |
| 库版本 | openai-whisper==20231117 |
| 参数量 | 244M |
| 主要语言 | 中文（普通话，zh） |
| 文件大小 | ~244MB |
| 默认缓存路径 | `~/.cache/whisper/small.pt` |

## 推理性能基准（CPU，单线程）

| 视频时长 | 转录耗时 | 实时倍率 |
|----------|----------|----------|
| 5 分钟 | ~75s | ~4× |
| 30 分钟 | ~450s | ~4× |
| 60 分钟 | ~900s | ~4× |

> 基准环境：Intel Xeon 4核，无 GPU。GPU（CUDA）可达 ~20× 实时。

## 配置项

```env
WHISPER_MODEL=small        # 可选: tiny / base / small / medium
WHISPER_DEVICE=cpu         # 可选: cpu / cuda
```

## 精度说明

- 普通话字符准确率（CER）：~90%+（标准口语教学环境）
- 噪声环境下准确率下降，`AUDIO_SNR_THRESHOLD_DB=10.0` 以下视为低质量

## 治理

- 模型文件通过 Whisper 官方 PyPI 包下载，不提交至 Git
- 版本固定为 `20231117`，升级须经测试验证后修改此文件
- 模型输出仅用于内部知识库提取，不对外暴露原始转录文本
