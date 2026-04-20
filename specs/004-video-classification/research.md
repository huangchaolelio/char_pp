# Research: COS 教学视频分类体系

**功能**: 004-video-classification
**日期**: 2026-04-20

## 决策记录

### D1 — 分类存储方式

**Decision**: 数据库新表 `video_classifications`（PostgreSQL）
**Rationale**: 与现有 AnalysisTask / ExpertTechPoint 体系统一，支持关联查询、人工修正追溯、按分类批量查询
**Alternatives considered**: 静态 YAML 快照（无法追溯修正）；纯内存（无持久化）

### D2 — 分类树定义方式

**Decision**: YAML 配置文件（`src/config/video_classification.yaml`），git 版本管理
**Rationale**: 乒乓球技术体系相对稳定，YAML 配置简单可读，无需 DB CRUD UI；变更可追溯
**Alternatives considered**: 数据库动态配置（过重）；Python 硬编码常量（修改需重部署且不可配置）

### D3 — 触发机制

**Decision**: 按需触发（手动 refresh + 任务提交时懒触发）
**Rationale**: 当前视频量 120 个，不需要复杂定时任务；与现有任务提交流程一致
**Alternatives considered**: 定时扫描（不必要的复杂度）

### D4 — ActionType 与分类的关系

**Decision**: 以 `video_classifications.action_type` 为准，废弃 `infer_action_type_hint()`
**Rationale**: 单一数据来源，避免两套逻辑不一致；原关键词逻辑迁移到分类服务内部
**Alternatives considered**: 两套逻辑并存（维护成本高）

---

## 120 视频 Ground Truth 分类表

> 用于验收测试：自动分类结果需与此表对比，tech_category 准确率 ≥95%，tech_detail 准确率 ≥85%

| 节 | 文件名 | tech_category | tech_detail | action_type | video_type |
|----|--------|---------------|-------------|-------------|------------|
| 01 | 第01节知行合一 前言.mp4 | 其他 | 前言/概述 | null | tutorial |
| 02 | 第02节横板握拍.mp4 | 握拍与姿态 | 横板握拍 | null | tutorial |
| 03 | 第03节直板握拍.mp4 | 握拍与姿态 | 直板握拍 | null | tutorial |
| 04 | 第04节姿态 屁股理论.mp4 | 握拍与姿态 | 站姿体态 | null | tutorial |
| 05 | 第05节找点辅助练习（时空结合）.mp4 | 其他 | 辅助练习 | null | tutorial |
| 06 | 第06节正手攻球.mp4 | 正手技术 | 正手攻球 | forehand_attack | tutorial |
| 07 | 第07节正手攻球 训练计划1-1,1-2.mp4 | 正手技术 | 正手攻球 | forehand_attack | training |
| 08 | 第08节正手两点跑位.mp4 | 正手技术 | 正手位置步法 | forehand_position | tutorial |
| 09 | 第09节正手两点 训练计划1-3.mp4 | 正手技术 | 正手位置步法 | forehand_position | training |
| 10 | 第10节横板反手推拨.mp4 | 反手技术 | 反手推拨 | backhand_push | tutorial |
| 11 | 第11节横板反手推拨 训练计划1-4.mp4 | 反手技术 | 反手推拨 | backhand_push | training |
| 12 | 第12节正反手基础搓球.mp4 | 综合技术 | 正反手搓球 | null | tutorial |
| 13 | 第13节基础搓球 训练计划1-6.mp4 | 搓球与摆短 | 正反手搓球（基础） | null | training |
| 14 | 第14节直板反手（横打、推挡）.mp4 | 反手技术 | 直板反手 | null | tutorial |
| 15 | 第15节直板反手 训练计划1-5.mp4 | 反手技术 | 直板反手 | null | training |
| 16 | 第16节第二阶段（基础步伐＋衔接）前言.mp4 | 其他 | 前言/概述 | null | tutorial |
| 17 | 第17节如何移动中找球（三个计划）左推右攻、三点跑位、推侧推正.mp4 | 步法与衔接 | 综合步法 | null | tutorial |
| 18 | 第18节三个计划 训练计划2-1，2-2，2-3.mp4 | 步法与衔接 | 综合步法 | null | training |
| 19 | 第19节正手不定点.mp4 | 正手技术 | 正手位置步法 | forehand_position | tutorial |
| 20 | 第20节两边衔接不定点.mp4 | 步法与衔接 | 综合不定点 | null | tutorial |
| 21 | 第21节正手、两边不定点 训练计划2-4，2-5.mp4 | 步法与衔接 | 综合不定点 | null | training |
| 22 | 第22节第三阶段 前言.mp4 | 其他 | 前言/概述 | null | tutorial |
| 23 | 第23节正手快带.mp4 | 正手技术 | 正手快带 | forehand_counter | tutorial |
| 24 | 第24节快带 训练计划3-1，3-2.mp4 | 正手技术 | 正手快带 | forehand_counter | training |
| 25 | 第25节同框架延续 正手.mp4 | 正手技术 | 正手拉球（弧圈） | forehand_topspin | tutorial |
| 26 | 第26节同框架延续 训练计划3-3，3-4.mp4 | 正手技术 | 正手拉球（弧圈） | forehand_topspin | training |
| 27 | 第27节正手连续拉.mp4 | 正手技术 | 正手拉球（弧圈） | forehand_topspin | tutorial |
| 28 | 第28节正手连续拉 训练计划 3-5、3-6.mp4 | 正手技术 | 正手拉球（弧圈） | forehand_topspin | training |
| 29 | 第29节发多球技巧.mp4 | 其他 | 多球技巧 | null | tutorial |
| 30 | 第30节正手拉球两点、三点跑位.mp4 | 正手技术 | 正手位置步法 | forehand_position | tutorial |
| 31 | 第31节正手拉球跑位 训练计划3-7，3-8.mp4 | 正手技术 | 正手位置步法 | forehand_position | training |
| 32 | 第32节推侧扑.mp4 | 步法与衔接 | 推侧扑 | null | tutorial |
| 33 | 第33节推侧扑 训练计划3-9.mp4 | 步法与衔接 | 推侧扑 | null | training |
| 34 | 第34节同框架延续 反手.mp4 | 反手技术 | 反手拉球 | backhand_topspin | tutorial |
| 35 | 第35节反手同框架 训练计划3-10、3-11.mp4 | 反手技术 | 反手拉球 | backhand_topspin | training |
| 36 | 第36节摆短.mp4 | 搓球与摆短 | 摆短（横板） | null | tutorial |
| 37 | 第37节直板摆短.mp4 | 搓球与摆短 | 摆短（直板） | null | tutorial |
| 38 | 第38节摆短 训练计划3-12.mp4 | 搓球与摆短 | 摆短（横板） | null | training |
| 39 | 第39节反手摆短.mp4 | 搓球与摆短 | 反手摆短 | null | tutorial |
| 40 | 第40节直拍反手摆短.mp4 | 搓球与摆短 | 反手摆短 | null | tutorial |
| 41 | 第41节反手摆短 训练计划3-13.mp4 | 搓球与摆短 | 反手摆短 | null | training |
| 42 | 第42节正手劈长.mp4 | 正手技术 | 正手劈长 | forehand_chop_long | tutorial |
| 43 | 第43节正手劈长 训练计划3-14.mp4 | 正手技术 | 正手劈长 | forehand_chop_long | training |
| 44 | 第44节反手劈长.mp4 | 反手技术 | 反手劈长 | null | tutorial |
| 45 | 第45节反手劈长 训练计划3-15.mp4 | 反手技术 | 反手劈长 | null | training |
| 46 | 第46节反手变线.mp4 | 反手技术 | 反手变线 | null | tutorial |
| 47 | 第47节反手变线 训练计划3-16.mp4 | 反手技术 | 反手变线 | null | training |
| 48 | 第48节基础防守.mp4 | 防守与保障 | 基础防守 | null | tutorial |
| 49 | 第49节运动前热身.mp4 | 体能与辅助 | 运动前热身 | null | tutorial |
| 50 | 第50节同框架延续 正手发力拉.mp4 | 正手技术 | 正手拉球（弧圈） | forehand_topspin | tutorial |
| 51 | 第51节正手发力 训练计划3-17.mp4 | 正手技术 | 正手拉球（弧圈） | forehand_topspin | training |
| 52 | 第52节放松拉伸.mp4 | 体能与辅助 | 放松拉伸 | null | tutorial |
| 53 | 第53节广式正手 发力传递.mp4 | 正手技术 | 正手拉球（弧圈） | forehand_topspin | tutorial |
| 54 | 第54节广式正手 核心秘密I.mp4 | 正手技术 | 正手拉球（弧圈） | forehand_topspin | tutorial |
| 55 | 第55节广式正手 核心秘密II.mp4 | 正手技术 | 正手拉球（弧圈） | forehand_topspin | tutorial |
| 56 | 第56节反手弹击.mp4 | 反手技术 | 反手弹击 | backhand_flick | tutorial |
| 57 | 第57节反手弹击 训练计划3-18.mp4 | 反手技术 | 反手弹击 | backhand_flick | training |
| 58 | 第58节正手起下旋.mp4 | 正手技术 | 正手起下旋 | forehand_loop_underspin | tutorial |
| 59 | 第59节正手起下旋 训练计划3-19、20、21.mp4 | 正手技术 | 正手起下旋 | forehand_loop_underspin | training |
| 60 | 第60节反手起下旋.mp4 | 反手技术 | 反手起下旋 | null | tutorial |
| 61 | 第61节反手起下旋 直拍横打.mp4 | 反手技术 | 反手起下旋 | null | tutorial |
| 62 | 第62节反手起下旋 训练计划2-22、23.mp4 | 反手技术 | 反手起下旋 | null | training |
| 63 | 第63节防高吊.mp4 | 防守与保障 | 防高吊 | null | tutorial |
| 64 | 第64节横拍转不转发球.mp4 | 发球 | 转不转发球 | null | tutorial |
| 65 | 第65节侧旋发球.mp4 | 发球 | 侧旋发球 | null | tutorial |
| 66 | 第66节偷长.mp4 | 搓球与摆短 | 偷长 | null | tutorial |
| 67 | 第67节勾手发球-短球.mp4 | 发球 | 勾手发球 | null | tutorial |
| 68 | 第68节勾手发球-偷长.mp4 | 发球 | 勾手发球 | null | tutorial |
| 69 | 第69节直板转不转发球.mp4 | 发球 | 转不转发球 | null | tutorial |
| 70 | 第70节转不转发球训练计划4-1.mp4 | 发球 | 转不转发球 | null | training |
| 71 | 第71节直板侧旋发球.mp4 | 发球 | 侧旋发球 | null | tutorial |
| 72 | 第72节直板侧旋训练计划4-2.mp4 | 发球 | 侧旋发球 | null | training |
| 73 | 第73节直板勾手发球.mp4 | 发球 | 勾手发球 | null | tutorial |
| 74 | 第74节直板勾手发球训练计划4-3.mp4 | 发球 | 勾手发球 | null | training |
| 75 | 第75节直板偷长发球.mp4 | 发球 | 勾手发球 | null | tutorial |
| 76 | 第76节直板偷长训练计划4-4.mp4 | 发球 | 勾手发球 | null | training |
| 77 | 第77节正手位挑球.mp4 | 正手技术 | 正手挑打 | forehand_flick | tutorial |
| 78 | 第78节正手挑打训练计划4-5.mp4 | 正手技术 | 正手挑打 | forehand_flick | training |
| 79 | 第79节推挑.mp4 | 搓球与摆短 | 偷长 | null | tutorial |
| 80 | 第80节推挑-实战型示范.mp4 | 搓球与摆短 | 偷长 | null | tutorial |
| 81 | 第81节撇挑.mp4 | 搓球与摆短 | 偷长 | null | tutorial |
| 82 | 第82节撇挑训练计划4-6.mp4 | 搓球与摆短 | 偷长 | null | training |
| 83 | 第83节反手拧拉.mp4 | 反手技术 | 反手拧拉 | null | tutorial |
| 84 | 第84节拧拉训练计划4-7.1.mp4 | 反手技术 | 反手拧拉 | null | training |
| 85 | 第85节拧拉训练计划4-7.2.mp4 | 反手技术 | 反手拧拉 | null | training |
| 86 | 第86节移动中拧拉训练4-7.3.mp4 | 反手技术 | 反手拧拉 | null | training |
| 87 | 第87节拧拉手腕训练4-7.4.mp4 | 反手技术 | 反手拧拉 | null | training |
| 88 | 第88节拧拉-直板横打.mp4 | 反手技术 | 反手拧拉 | null | tutorial |
| 89 | 第89节林昀儒的拧拉教学揭秘.mp4 | 反手技术 | 反手拧拉 | null | tutorial |
| 90 | 第90节转不转接发球训练-上集.mp4 | 接发球 | 转不转接发球 | null | training |
| 91 | 第91节转不转接发球训练-下集.mp4 | 接发球 | 转不转接发球 | null | training |
| 92 | 第92节侧上旋接发球训练-上集.mp4 | 接发球 | 侧上旋接发球 | null | training |
| 93 | 第93节侧上旋接发球训练-下集.mp4 | 接发球 | 侧上旋接发球 | null | training |
| 94 | 第94节侧下旋接发球训练.mp4 | 接发球 | 侧下旋接发球 | null | training |
| 95 | 第95节勾手下旋接发球.mp4 | 接发球 | 侧下旋接发球 | null | tutorial |
| 96 | 第96节勾手侧上旋接发球训练.mp4 | 接发球 | 侧上旋接发球 | null | training |
| 97 | 第97节反手位急长接发球训练.mp4 | 接发球 | 反手位急长接发球 | null | training |
| 98 | 第98节正手半出台接发球训练-上集.mp4 | 接发球 | 正手半出台接发球 | null | training |
| 99 | 第99节正手位半出台接发球训练-下集.mp4 | 接发球 | 正手半出台接发球 | null | training |
| 100 | 第100节接发球站位体系-上集.mp4 | 接发球 | 接发球综合 | null | tutorial |
| 101 | 第101节接发球站位体系-下集.mp4 | 接发球 | 接发球综合 | null | tutorial |
| 102 | 第102节接发球的万能方式.mp4 | 接发球 | 接发球综合 | null | tutorial |
| 103 | 第103节进阶发球-逆旋转发球1.0.mp4 | 发球 | 逆旋转发球 | null | tutorial |
| 104 | 第104节进阶发球-逆旋转2.0.mp4 | 发球 | 逆旋转发球 | null | tutorial |
| 105 | 第105节进阶发球-逆旋转3.0.mp4 | 发球 | 逆旋转发球 | null | tutorial |
| 106 | 第106节逆旋发球-训练方法1.0.mp4 | 发球 | 逆旋转发球 | null | training |
| 107 | 第107节逆旋发球-训练方法2.0.mp4 | 发球 | 逆旋转发球 | null | training |
| 108 | 第108节逆旋发球-训练方法3.0.mp4 | 发球 | 逆旋转发球 | null | training |
| 109 | 第109节逆下旋-训练方法4.0.mp4 | 发球 | 逆旋转发球 | null | training |
| 110 | 第110节逆上旋-训练方法5.0.mp4 | 发球 | 逆旋转发球 | null | training |
| 111 | 第111节逆旋偷长-训练方法6.0.mp4 | 发球 | 逆旋转发球 | null | training |
| 112 | 第112节逆旋发球-战术搭配套路.mp4 | 发球 | 逆旋转发球 | null | tutorial |
| 113 | 第113节孙教练教林昀儒的发球秘密.mp4 | 发球 | 林昀儒式发球 | null | tutorial |
| 114 | 第114节林昀儒的发球-训练计划1.0.mp4 | 发球 | 林昀儒式发球 | null | training |
| 115 | 第115节林昀儒发球-训练计划2.0.mp4 | 发球 | 林昀儒式发球 | null | training |
| 116 | 第116节林昀儒发球-训练计划3.0.mp4 | 发球 | 林昀儒式发球 | null | training |
| 117 | 第117节林昀儒发球-训练计划4.0.mp4 | 发球 | 林昀儒式发球 | null | training |
| 118 | 第118节绳梯训练6-1.mp4 | 体能与辅助 | 步法热身（绳梯） | null | training |
| 119 | 第119节绳梯训练6-2.mp4 | 体能与辅助 | 步法热身（绳梯） | null | training |
| 120 | 第120节绳梯训练6-3.mp4 | 体能与辅助 | 步法热身（绳梯） | null | training |

## 分类统计（ground truth）

| tech_category | 视频数 | 有 action_type | action_type=null |
|---------------|--------|----------------|-----------------|
| 正手技术 | 27 | 27 | 0 |
| 反手技术 | 22 | 4（push/topspin/flick） | 18 |
| 步法与衔接 | 7 | 0 | 7 |
| 搓球与摆短 | 10 | 0 | 10 |
| 发球 | 22 | 0 | 22 |
| 接发球 | 12 | 0 | 12 |
| 握拍与姿态 | 3 | 0 | 3 |
| 防守与保障 | 2 | 0 | 2 |
| 体能与辅助 | 5 | 0 | 5 |
| 综合技术 | 1 | 0 | 1 |
| 其他 | 9 | 0 | 9 |
| **合计** | **120** | **31** | **89** |

## 关键词覆盖分析

### 需要特殊处理的边界案例

| 视频 | 问题 | 处理方案 |
|------|------|----------|
| 第12节正反手基础搓球 | 正反手组合词 | exclude_keywords: ["正反手", "正反"] → 归入"综合技术" |
| 第25/26节同框架延续 正手 | "正手"关键词，但标题无具体技术词 | 大类命中，细分用"同框架"→正手拉球，confidence=0.7 |
| 第34/35节同框架延续 反手 | 同上，反手 | 大类命中，细分用"同框架"→反手拉球，confidence=0.7 |
| 第13节基础搓球 训练计划 | 无明确正/反手词 | 搓球关键词"搓球"→搓球与摆短 |
| 第17节如何移动中找球 | 多步法类型混合 | 步法关键词"跑位"/"不定点"→步法与衔接 |
| 第29节发多球技巧 | 非标准技术类 | 无主要技术关键词→其他 |
| 第66/79/80/81节偷长/推挑/撇挑 | 偷长属于摆短类 | "偷长"/"推挑"/"撇挑"→搓球与摆短 |
| 第113节孙教练教林昀儒的发球秘密 | 无明确发球类型 | "林昀儒"/"秘密"→林昀儒式发球，confidence=0.7 |
| 第83-89节拧拉系列 | "拧拉"keyword，无"反手"前缀 | 直接匹配"拧拉"→反手拧拉（拧拉默认反手技术） |

### YAML 关键词规则摘要

**正手技术识别**（require: "正手"，exclude: "正反手"/"正反"）：
- 位置步法：两点 / 跑位 / 不定点
- 攻球：攻球
- 快带：快带
- 劈长：劈长
- 挑打：挑打 / 挑球 / 位挑球
- 起下旋：起下旋
- 拉球（弧圈）：连续拉 / 发力拉 / 广式 / 拉球 / 同框架
- fallback → forehand_general

**反手技术识别**（require: "反手"，exclude: "正反手"/"正反"）：
- 弹击：弹击
- 推拨：推拨
- 劈长：劈长
- 变线：变线
- 起下旋：起下旋
- 拉球：同框架（反手版本）
- 直板反手：直板反手
- 拧拉（无"反手"前缀，直接匹配）：拧拉

**其他大类关键词**：
- 搓球与摆短：搓球 / 摆短 / 偷长 / 推挑 / 撇挑
- 步法与衔接：移动中 / 三点 / 推侧扑 / 两边衔接
- 发球：发球（非接发球）
- 接发球：接发球
- 握拍与姿态：握拍 / 姿态 / 屁股理论
- 体能与辅助：热身 / 拉伸 / 绳梯
- 防守与保障：防守 / 防高吊
