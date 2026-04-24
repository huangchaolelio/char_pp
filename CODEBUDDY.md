# charhuang_pp_cn 开发指南

乒乓球 AI 智能教练系统 — 后端分析服务。最后更新：2026-04-24

---

## 项目概述

基于计算机视觉 + 语音识别 + LLM 的乒乓球教学分析平台。核心能力：
- 教练视频自动分析 → 提取技术要点 → 构建知识库
- 运动员视频诊断 → 偏差计算 → AI 改进建议
- COS 全量视频分类管理（1015 个视频，21 类技术）

---

## 技术栈

| 层次 | 技术 | 版本 |
|------|------|------|
| Web 框架 | FastAPI + uvicorn | 0.111+ / 0.29+ |
| 数据库 | PostgreSQL (asyncpg) | 2.0+ (SQLAlchemy async) |
| 异步任务 | Celery + Redis | 5.4+ |
| 姿态估计 | YOLOv8 (GPU 优先) / MediaPipe (CPU fallback) | 8.0+ / 0.10+ |
| 语音识别 | Whisper | 20231117 |
| LLM | Venus Proxy (优先) / OpenAI | GPT-4 级别 |
| 云存储 | 腾讯云 COS SDK | 1.9.30+ |
| Python | 3.11+ | 项目虚拟环境 |

---

## 项目结构

```text
charhuang_pp_cn/
├── src/
│   ├── api/
│   │   ├── main.py               # FastAPI 应用入口，端口 8080
│   │   ├── routers/              # 9 个路由模块 (tasks, knowledge_base, videos,
│   │   │                         #   classifications, coaches, standards,
│   │   │                         #   diagnosis, teaching_tips, calibration)
│   │   └── schemas/              # 7 个 Pydantic 请求/响应模型
│   ├── models/                   # 19 个 SQLAlchemy ORM 模型
│   ├── services/                 # 25 个业务服务模块
│   ├── workers/
│   │   ├── celery_app.py
│   │   ├── expert_video_task.py  # 教练视频处理（11 步流水线）
│   │   ├── athlete_video_task.py # 运动员视频处理
│   │   └── classification_task.py # COS 扫描分类 Celery task
│   ├── db/
│   │   ├── session.py            # async_session_factory
│   │   └── migrations/           # Alembic 迁移（0001~0011）
│   ├── config/
│   │   ├── video_classification.yaml  # 12 教练规则 + 21 类技术规则
│   │   └── keywords/tech_hint_keywords.json
│   └── config.py                 # Pydantic Settings（全局配置入口）
├── config/
│   ├── coach_directory_map.json  # COS 目录名 → 教练姓名静态映射（20 条）
│   └── tech_classification_rules.json  # 21 类技术关键词规则（顺序敏感）
├── specs/                        # 功能规范（001~012，均已完成）
├── docs/
│   ├── architecture.md           # 技术架构文档
│   └── features.md               # 产品功能文档
├── tests/                        # unit / integration / contract
├── .codebuddy/skills/refresh-docs/ # 刷新文档 skill
├── pyproject.toml
├── alembic.ini
└── .env                          # 不提交到 git
```

---

## 常用命令

```bash
# Python 环境（必须用项目 3.11，不用系统默认 3.9）
source /opt/conda/envs/coaching/bin/activate
# 或直接指定：/opt/conda/envs/coaching/bin/python3.11

# 启动 API 服务
setsid uvicorn src.api.main:app --host 0.0.0.0 --port 8080 --reload &

# 重启服务（代码修改后必须重启，无热重载）
pkill -f "uvicorn src.api.main" && setsid uvicorn src.api.main:app --host 0.0.0.0 --port 8080 &

# 启动 Celery Worker
celery -A src.workers.celery_app worker --loglevel=info --concurrency=2

# 数据库迁移
alembic upgrade head
alembic revision --autogenerate -m "描述"

# 运行测试
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/unit/ -v
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/integration/ -v

# 安装依赖
/opt/conda/envs/coaching/bin/pip install -e ".[dev]"
```

---

## 核心配置

所有敏感配置通过 `.env` 注入，`src/config.py` 的 `Settings` 类读取：

| 配置项 | 说明 |
|--------|------|
| `DATABASE_URL` | PostgreSQL 连接串 |
| `REDIS_URL` | Redis 地址（默认 localhost:6379/0）|
| `COS_SECRET_ID/KEY` | 腾讯云 COS 凭证 |
| `COS_BUCKET` / `COS_REGION` | COS 存储桶 |
| `COS_VIDEO_PREFIX` | 旧路径（孙浩泓120集，Feature-001 遗留）|
| `COS_VIDEO_ALL_COCAH` | 全量教练视频根路径（`charhuang/tt_video/乒乓球合集【较新】/`）|
| `VENUS_TOKEN` / `VENUS_BASE_URL` | Venus Proxy（LLM 优先级高于 OpenAI）|
| `OPENAI_API_KEY` / `OPENAI_MODEL` | OpenAI fallback |
| `WHISPER_MODEL` | 默认 `small`（中文）|
| `POSE_BACKEND` | `auto`（YOLOv8 GPU → MediaPipe CPU）|

---

## 代码风格

### Python
- **类型注解**：所有函数参数和返回值必须有类型注解（Python 3.10+ union 写法 `X | None`）
- **异步优先**：数据库操作全部用 `async/await` + `async_session_factory`
- **Pydantic v2**：Schema 模型用 `model_config = ConfigDict(...)`，禁止 v1 语法
- **枚举**：技术类别用 `TECH_CATEGORIES` 枚举（定义在 `src/services/tech_classifier.py`），禁止用字符串字面量散落代码
- **服务层**：业务逻辑放 `src/services/`，路由层只做参数校验和响应组装，不含业务逻辑
- **错误处理**：服务层抛 `ValueError` / 自定义异常，路由层统一转 `HTTPException`
- **日志**：用标准 `logging`，不用 `print`

### 数据库
- 所有模型继承 `Base`，表名蛇形命名
- 关联关系必须有外键约束，索引声明在模型内
- 迁移文件命名：`NNNN_描述.py`（Alembic 自动生成）
- **不用同步 session**，统一用 `AsyncSession`

### API
- 版本前缀统一：`/api/v1/`
- 路由文件对应一个资源（coaches, videos, tasks...），不混搭
- 分页参数统一：`page` + `page_size`（默认 20，最大 100）
- 响应体统一包装：成功返回数据本身或 `{"data": ..., "total": N}`

---

## 关键设计决策

### 两张视频分类表并存
| 表 | 来源 Feature | 维护者 | 说明 |
|----|-------------|--------|------|
| `video_classifications` | Feature-004 | `VideoClassifierService` + refresh API | 老表，yaml 规则分类，12 教练 |
| `coach_video_classifications` | Feature-008 | `CosClassificationScanner` | 新表，COS 全量，21 类技术 + `kb_extracted` 字段 |

两表并行维护，不合并。

### 教练-目录映射规则
- 一个 COS 目录 = 一个独立教练实体
- 同 base_name 的多目录：第 1 个保持原名，后续加 `_2`、`_3` 后缀
- bio 来源：目录名本身
- 静态映射：`config/coach_directory_map.json`（20 条目录 → 姓名）

### LLM 调用优先级
Venus Proxy（`VENUS_TOKEN`）优先 → OpenAI fallback。两者共用 OpenAI 接口格式。

### 姿态估计降级策略
`auto` 模式：YOLOv8 GPU（首选，COCO AP≈50.4）→ MediaPipe CPU（fallback，33 关键点）

### COS 视频扫描
- 全量：`COS_VIDEO_ALL_COCAH` 前缀分页列举（`MaxKeys=1000`，循环 `NextMarker`）
- 增量：对比已存在 `cos_object_key`，只处理新增
- 零字节文件（目录占位符）自动跳过

---

## 文件组织规则

- **新脚本/分析文件**：放 `specs/NNN-xxx/scripts/` 或 `specs/NNN-xxx/research.md`，不得散落根目录
- **临时调试文件**：仅用 `/tmp/`，不提交 git
- **新 Feature 规范**：遵循 `specs/speckit.constitution.md` 结构（spec.md + plan.md + data-model.md + tasks.md）
- **文档**：放 `docs/`，用 `/refresh-docs` skill 自动更新

---

## 21 类技术分类（TECH_CATEGORIES）

```
forehand_push_long       forehand_attack          forehand_topspin
forehand_topspin_backspin forehand_loop_fast      forehand_loop_high
forehand_flick           backhand_attack          backhand_topspin
backhand_topspin_backspin backhand_flick          backhand_push
serve                    receive                   footwork
forehand_backhand_transition  defense             penhold_reverse
stance_posture           general                   unclassified
```

> `tech_classification_rules.json` 规则**顺序敏感**：精细类（如 `forehand_topspin_backspin`）必须在通用类（如 `forehand_topspin`）之前。

---

## 知识库提取工作流

1. 查询待处理视频：`GET /api/v1/classifications?tech_category=X&kb_extracted=false`
2. 提交任务：`POST /api/v1/tasks`（expert 类型）
3. 任务完成后标记：`PATCH /api/v1/classifications/{id}` 设 `kb_extracted=true`

---

## 活跃 Features（001~012，均已完成）

| # | Feature | 核心 API |
|---|---------|----------|
| 001 | 视频教练顾问 | `POST /tasks`, `GET /tasks/{id}` |
| 002 | 音频增强知识库提取 | Whisper 转录 → 技术关键词 |
| 003 | Skill 知识库→参考视频 | `GET /knowledge-base` |
| 004 | 视频分类体系 | `POST /videos/classifications/refresh` |
| 005 | 音频知识库教学建议 | `GET /teaching-tips` |
| 006 | 多教练知识库 | `GET /coaches`, `GET /calibration` |
| 007 | 处理速度优化 | 并行预分割，并发 2 |
| 008 | 教练视频技术分类DB | `POST /classifications/scan`, `GET /classifications` |
| 009 | SQL 查询脚本 | `specs/009-sql-query-scripts/` |
| 010 | 构建技术标准 | `POST /standards/build`, `GET /standards` |
| 011 | 运动员动作诊断 | `POST /diagnosis`, `GET /diagnosis/{id}` |
| 012 | 全量任务查询接口 | `GET /tasks?page=1&page_size=20&status=X` |

---

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
