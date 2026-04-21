# Research: 教练视频技术分类数据库 (Feature 008)

## 技术决策

### Decision 1: 分类引擎 — 关键词规则优先 + LLM 兜底

**Decision**: 两阶段分类：先用关键词规则字典匹配（确定性），规则未命中时调用 venus_proxy LLM 推断。

**Rationale**:
- 关键词规则：零延迟、零成本、可解释、可版本化；适合"正手攻球"/"正手劈长"这类中文乒乓技术术语
- LLM 兜底：处理新词/模糊描述，如"右脚找位解析"本质是正手攻球的子环节
- 每条记录标注 `classification_source: rule | llm | manual`，置信度 0.0-1.0

**Alternatives considered**:
- 纯 LLM：成本高、延迟不稳定、批量扫描 500 条会显著慢
- 纯规则：unclassified 比率可能超过 5%，不满足 SC-001

**关键词规则设计** (`config/tech_classification_rules.json`):
```json
{
  "forehand_push_long":    ["劈长"],
  "forehand_attack":       ["正手攻球", "正手攻"],
  "forehand_topspin":      ["正手拉球", "正手上旋拉球", "正手弧圈"],
  "forehand_topspin_backspin": ["正手拉下旋", "正手下旋拉球"],
  "forehand_loop_fast":    ["前冲弧圈"],
  "forehand_loop_high":    ["高调弧圈"],
  "forehand_flick":        ["正手拧拉", "台内挑打", "正手挑"],
  "backhand_attack":       ["反手攻球", "反手攻"],
  "backhand_topspin":      ["反手拉球", "反手上旋拉球"],
  "backhand_topspin_backspin": ["反手拉下旋", "反手下旋拉球"],
  "backhand_flick":        ["反手弹击", "快撕", "近台弹击"],
  "backhand_push":         ["推挡", "搓球", "反手推"],
  "serve":                 ["发球"],
  "receive":               ["接发球"],
  "footwork":              ["步法", "步伐", "移动"],
  "forehand_backhand_transition": ["正反手转换", "转换"],
  "defense":               ["防守", "防弧圈", "防快攻"],
  "penhold_reverse":       ["直拍横打"],
  "stance_posture":        ["站位", "姿态", "握拍", "姿势"],
  "general":               ["综合", "前言", "总结", "实战"]
}
```

**匹配规则**: 按顺序扫描键（更精细的优先），第一个命中为主技术，继续扫描余下关键词填入 tech_tags。

---

### Decision 2: 教练名映射 — 静态配置文件

**Decision**: `config/coach_directory_map.json` 存储目录名到教练姓名的映射，运行时加载，无匹配时回退使用目录全名并标记 `name_source=fallback`。

**Rationale**: COS 目录名格式不统一（含课程集数、品牌名等），正则难以稳定覆盖所有情况；静态映射准确、可维护、一次配置。

**初始映射**:
```json
{
  "《知行合一》孙浩泓专业乒乓球全套教学课程120集": "孙浩泓",
  "【孙霆无解勾手发球】勾手发球教学和讲解": "孙霆",
  "专业乒乓球系统课程_高云娇42节": "高云娇",
  "中国国家乒乓球队国手尹航教学合集_尹航19节": "尹航",
  "全世爆乒乓专业乒乓球教学课 101节": "全世爆",
  "全套技术教学大合集_源动力沙指导250节": "沙指导",
  "国手王增羿直拍反手五分钟技术爆炸": "王增羿",
  "学习国手全套乒乓球技术_爱乒乓的穆静毓56节": "穆静毓",
  "小孙专业乒乓球——全套接发球课程_24节": "孙浩泓",
  "小孙专业乒乓球——全套步伐课程_8节": "孙浩泓",
  "小孙专业乒乓球—全套实战比赛技战术技巧应用课程 17节": "孙浩泓",
  "小孙专业乒乓球—全套正反手体系课程_33节": "孙浩泓",
  "小孙专业乒乓球全套发球课程_22节": "孙浩泓",
  "张继科 · 乒乓球大师课_张继科13节": "张继科",
  "王开精品教学": "王开",
  "王教练乒乓球教学体系_全世爆健硕体育106节": "全世爆",
  "直拍横打【中远台拉球技术】15节": "unknown",
  "直拍横打技术 从0-1 初学者入门课程10节": "unknown",
  "直板名将张蔷前国手课程38节": "张蔷",
  "郭焱乒乓球教学-课程全集_郭焱 107节": "郭焱"
}
```

---

### Decision 3: 数据库表设计 — 新增独立表

**Decision**: 新建 `coach_video_classifications` 表，不扩展现有表。迁移版本 0009。

**Rationale**: 技术分类数据与已有的 `analysis_tasks`（处理任务）和 `video_classifications`（视频类型分类）职责不同，独立表避免耦合，便于后续知识库提取功能引用。

**表结构**:
```sql
CREATE TABLE coach_video_classifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coach_name      VARCHAR(100) NOT NULL,
    course_series   VARCHAR(255) NOT NULL,   -- COS 目录名
    cos_object_key  VARCHAR(1024) NOT NULL UNIQUE,
    filename        VARCHAR(255) NOT NULL,
    tech_category   VARCHAR(64) NOT NULL,    -- 主技术类别 ID
    tech_tags       TEXT[] NOT NULL DEFAULT '{}',  -- 副技术标签
    raw_tech_desc   VARCHAR(255),            -- 从文件名提取的原始描述
    classification_source VARCHAR(10) NOT NULL DEFAULT 'rule',  -- rule|llm|manual
    confidence      FLOAT NOT NULL DEFAULT 1.0,
    duration_s      INTEGER,                 -- 视频时长（秒）
    name_source     VARCHAR(10) NOT NULL DEFAULT 'map',  -- map|fallback
    kb_extracted    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_cvclf_coach ON coach_video_classifications(coach_name);
CREATE INDEX idx_cvclf_tech ON coach_video_classifications(tech_category);
CREATE INDEX idx_cvclf_kb ON coach_video_classifications(kb_extracted);
```

---

### Decision 4: Celery Task 设计 — 新增独立扫描 task

**Decision**: 新增 `src/workers/classification_task.py`，包含两个 task：`scan_cos_videos_full` 和 `scan_cos_videos_incremental`。沿用 `@shared_task` 模式。

**Rationale**: 扫描任务与现有教练/运动员视频处理任务完全独立，独立文件便于维护。

**全量扫描逻辑**:
1. 清空或标记旧记录（soft）→ 重新扫描所有 COS 目录
2. 分页遍历 COS（每页 1000 条）
3. 对每个 `.mp4` 文件：提取教练名、分类技术、写入数据库（upsert by cos_object_key）

**增量扫描逻辑**:
1. 从数据库取已有的 cos_object_key 集合
2. 遍历 COS，跳过已存在的 key，只处理新文件

---

### Decision 5: LLM 分类 Prompt 设计

**Decision**: 将文件名和课程系列名一并提供给 LLM，要求从技术类别 ID 列表中选一个最合适的。

**Prompt 模板** (用于 `classification_source=llm` 的兜底分类):
```
你是一位乒乓球技术分类专家。根据以下视频文件名和所属课程系列，判断该视频教学的主要技术类别。

课程系列：{course_series}
视频文件名：{filename}

可选技术类别（从中选择一个最匹配的 ID）：
{tech_category_list}

请以 JSON 格式回答：
{"tech_category": "<类别ID>", "confidence": 0.0-1.0, "reason": "一句话说明"}

只输出 JSON，不要其他内容。
```

**成本估算**: 每条 LLM 调用约 ~200 tokens，假设 unclassified 率 10%（50条），总消耗约 10k tokens，venus_proxy 成本可忽略。

---

## 现有代码复用

| 组件 | 复用方式 |
|------|----------|
| `src/services/cos_client.py` | 直接复用 CosClient，调用 `list_objects` 分页遍历 |
| `src/services/llm_client.py` | 直接复用 LlmClient，调用 `client.chat()` 进行兜底分类 |
| `src/workers/celery_app.py` | 直接复用 celery 实例和配置 |
| `src/config.py` | 直接复用 `get_settings()`，cos_video_all_cocah 路径已在 .env 中配置 |
| `src/api/routers/tasks.py` 的路由模式 | 参考 POST /tasks/expert-video 的 202 返回模式 |
| `src/db/session.py` 的 Base | 新 ORM 模型继承同一 Base |

## 章程合规性检查

| 原则 | 状态 |
|------|------|
| 规范驱动开发（spec.md 存在） | ✅ |
| 无前端任务 | ✅ |
| 增量交付（P1 可独立测试） | ✅ |
| 可观测性（结构化日志） | ✅（需在实现中确保） |
| AI 模型治理（LLM 兜底有置信度标注） | ✅ |
| 量化精准度指标（SC-002: ≥90%） | ✅ |
| Python 环境隔离（coaching venv） | ✅ |
| YAGNI（无定时调度，无额外抽象） | ✅ |
