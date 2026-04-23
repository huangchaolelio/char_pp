# SQL 查询脚本索引

常用 PostgreSQL 查询脚本，按功能分类整理，用于排查定位和日常数据分析。

## 快速使用

```bash
# 连接数据库
psql -h 127.0.0.1 -p 5432 -U postgres -d coaching_db

# 执行某个脚本（以 A 类为例）
psql -h 127.0.0.1 -p 5432 -U postgres -d coaching_db \
  -f specs/009-sql-query-scripts/queries/A-task-progress.sql

# 执行单条查询（复制粘贴到 psql）
# 注意修改 REPLACE 注释标注的参数
```

---

## 脚本目录

### [A-task-progress.sql](queries/A-task-progress.sql) — 任务进度与状态

| 编号 | 查询名 | 用途 |
|------|--------|------|
| A1 | 全局任务状态汇总 | 各状态（pending/processing/success/failed）数量和占比 |
| A2 | 僵尸任务检查 | processing 状态但超过 30 分钟无进展的卡死任务 |
| A3 | 失败任务列表 | failed/rejected 任务及错误信息，可按技术类别筛选 |
| A4 | 处理速率统计 | 最近 24 小时每小时完成任务数 |
| A5 | 指定类别 KB 进度 | 某技术类别的已提取/待提取数量 |

---

### [B-video-classification.sql](queries/B-video-classification.sql) — 视频分类统计

| 编号 | 查询名 | 用途 |
|------|--------|------|
| B1 | 所有类别提取进度 | 21 个技术类别的视频数和完成率一览 |
| B2 | 各教练提取进度 | 每位教练的视频总数和 KB 提取完成率 |
| B3 | 待提取视频列表 | 指定技术类别下 kb_extracted=false 的完整列表 |
| B4 | 技术类别教练分布 | 某技术类别下各教练的视频分布 |
| B5 | 分类来源分布 | rule/llm/manual 各来源占比，评估分类质量 |

---

### [C-coach-chain.sql](queries/C-coach-chain.sql) — 按教练查全链路

> REPLACE 参数：`'沙指导'` → 目标教练姓名

| 编号 | 查询名 | 用途 |
|------|--------|------|
| C1 | 教练视频及任务状态 | 该教练所有视频 + 最新任务状态 + 错误信息 |
| C2 | 教练 teaching_tips | 该教练所有高置信度教学建议，按技术阶段排列 |
| C3 | 教学建议数量统计 | 该教练各技术类别和阶段的建议数量汇总 |
| C4 | 完整链路状态 | 视频 → 任务 → 转录 → KB 一张表全览 |

**可用教练名**: 沙指导、全世爆、孙浩泓、高云娇、尹航、张蔷、张继科、穆静毓、王增羿、郭焱 等

---

### [D-tech-knowledge-base.sql](queries/D-tech-knowledge-base.sql) — 按技术类别查知识库

> REPLACE 参数：`'forehand_topspin'` → 目标技术类别

| 编号 | 查询名 | 用途 |
|------|--------|------|
| D1 | 技术类别提取进度 | 该类别各教练的已提取/待提取统计 |
| D2 | 所有 teaching_tips | 该类别高置信度教学建议（按教练和阶段） |
| D3 | 各教练建议数对比 | 横向对比各教练在各技术阶段的贡献 |
| D4 | 技术参数要点 | expert_tech_points 各维度 min/ideal/max 参数 |
| D5 | 知识库版本列表 | 涉及该技术的知识库版本状态 |

**可用技术类别**:
```
forehand_topspin          正手拉球/上旋
forehand_topspin_backspin 正手拉下旋
forehand_loop_fast        正手前冲弧圈
forehand_loop_high        正手高调弧圈
forehand_flick            正手拧拉/台内挑打
forehand_attack           正手攻球
forehand_push_long        正手劈长
backhand_topspin          反手拉球/上旋
backhand_topspin_backspin 反手拉下旋
backhand_flick            反手弹击/快撕/拧拉
backhand_push             反手推挡/搓球
backhand_attack           反手攻球
serve                     发球
receive                   接发球
footwork                  步法
forehand_backhand_transition 正反手转换
defense                   防守
penhold_reverse           直拍横打
stance_posture            站位/姿态
general                   综合/通用
```

---

### [E-audio-transcripts.sql](queries/E-audio-transcripts.sql) — 音频转录

| 编号 | 查询名 | 用途 |
|------|--------|------|
| E1 | 转录质量分布 | 全局 ok/low_snr/silent 占比 |
| E2 | 指定类别转录概况 | 某技术类别下每个视频的转录质量和时长 |
| E3 | 缺少转录的成功任务 | 任务成功但无转录记录，排查音频降级 |
| E4 | 指定任务转录详情 | 查看某任务的转录句子内容（JSONB） |

---

### [F-kb-versions-overview.sql](queries/F-kb-versions-overview.sql) — 知识库版本管理 & 综合统计

| 编号 | 查询名 | 用途 |
|------|--------|------|
| F1 | 知识库版本列表 | 所有版本状态、要点数、审核信息 |
| F2 | 全局提取进度汇总 | 所有技术类别一张表展示完整进度 |
| F3 | teaching_tips 全局统计 | 各技术类别建议数、教练覆盖数、置信度分布 |
| F4 | 孤立任务检查 | 无对应分类记录的 analysis_task（数据完整性） |
| F5 | 各教练全链路摘要 | 所有教练的视频/任务/转录/KB 完整性一览 |

---

## 常用参数说明

| 参数 | 说明 | 示例 |
|------|------|------|
| `tech_category` | 技术类别 ID | `'forehand_topspin'` |
| `coach_name` | 教练姓名 | `'沙指导'` |
| `confidence` | 置信度阈值 | `>= 0.9`（高质量过滤） |
| `INTERVAL` | 时间窗口 | `'30 minutes'`, `'24 hours'` |
| `task_id` | 任务 UUID | `'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'` |

## 关键表关系

```
coach_video_classifications
  └── cos_object_key ←→ analysis_tasks.video_storage_uri
                              └── id ←→ teaching_tips.task_id
                              └── id ←→ audio_transcripts.task_id
                              └── id ←→ expert_tech_points.source_video_id
                              └── knowledge_base_version → tech_knowledge_bases.version
```
