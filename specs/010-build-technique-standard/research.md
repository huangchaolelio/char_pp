# 研究报告: 构建单项技术标准知识库

**功能**: 010-build-technique-standard
**日期**: 2026-04-22

## 决策 1: 聚合算法

**Decision**: 中位数 + 百分位数（ideal=中位数，min=P25，max=P75）

**Rationale**: 乒乓球技术参数（如击球时机、转腰角度等）在不同教练的表述中可能存在合理差异，中位数比均值对异常值更鲁棒。P25/P75 四分位数范围是统计学上描述"正常范围"的标准方法，与医学正常值、工程公差等领域实践一致。

**Alternatives considered**:
- 均值 ± 标准差：直觉直接，但受极端值拉偏，且教练样本量小时标准差不稳定
- 置信度加权均值：逻辑合理但引入额外复杂度；在 confidence 分布集中（≥0.7 已过滤）时提升有限
- 专家投票众数：适合离散枚举量，不适合连续参数（如角度、时间）

---

## 决策 2: conflict_flag 数据处理

**Decision**: 排除 conflict_flag=true 的技术点，不参与聚合

**Rationale**: conflict_flag 标识音频与视频来源给出矛盾信息的技术点，其可靠性存疑。纳入会引入不确定性噪声，而排除不影响标准覆盖率（冲突比例通常极低）。

**Alternatives considered**:
- 降权纳入：逻辑合理，但增加算法复杂度且权重因子难以合理定义
- 全量纳入（忽略 conflict_flag）：简单，但可能污染标准参数

---

## 决策 3: 数据不足阈值

**Decision**: 每个技术类别至少来自 **2 位不同教练** 的技术点，才可构建多源标准；1 位教练可构建单源标准；0 位则跳过

**Rationale**: "多教练共识"是技术标准有效性的基本保证。2 位教练是最低共识要求，与医疗指南、体育标准中"至少两个独立来源"的惯例一致。阈值为 2 还使"单源"vs"多源"的区分有意义。

**Alternatives considered**:
- 每维度至少 3 个点：过于严格，早期数据稀少时无法生成任何标准
- 每技术至少 3 位教练：早期阶段可能大量技术无法建标，影响系统可用性

---

## 决策 4: 缺失维度处理

**Decision**: 只生成有数据的维度，无数据维度不生成 TechStandardPoint 记录

**Rationale**: 技术标准应反映已有共识，强行为缺失维度填入默认值或 NULL 记录会误导下游诊断比对。诊断模块在遇到无对应 TechStandardPoint 的维度时，可直接跳过该维度比对，逻辑清晰。

**Alternatives considered**:
- 生成全部维度并标记 data_available=false：增加数据模型复杂度，诊断模块需额外判断
- 按每维度单独设阈值：复杂度过高，与整体技术阈值不一致

---

## 决策 5: 触发方式

**Decision**: 仅支持手动触发（API 调用）或外部调度驱动，本功能不内置定时调度

**Rationale**: 知识库数据更新频率低（需人工审核视频提取结果），不需要高频自动刷新。内置调度会引入 Celery/定时任务等额外依赖和运维复杂度。外部调度（如 cron + API 调用）更灵活，符合 YAGNI 原则。

**Alternatives considered**:
- 定时自动 + 手动：引入调度依赖，当前规模不必要
- 知识库更新自动触发：需监听 ExpertTechPoint 写入事件，引入事件驱动架构复杂度

---

## 技术依赖确认

- **统计库**: Python 标准库 `statistics` 模块（median）+ `numpy`（percentile）；numpy 已是项目间接依赖
- **数据库**: PostgreSQL + SQLAlchemy asyncio（现有栈），新增 2 张表
- **迁移**: Alembic（现有），新建迁移文件 `0010_tech_standard.py`
- **API**: FastAPI（现有），新增 router `src/api/routers/standards.py`
- **最新迁移版本**: 0009（coach_video_classifications）
