# 环境安装指南

> 目标读者：第一次在全新机器上部署本项目的后端开发 / 联调 / 运维同学。  
> 涵盖两条等价的安装路径：**Conda 主路径（推荐）** 与 **uv 加速路径**。两条路径产出的运行态完全一致，任选其一即可。

---

## 1. 事实基线

| 组件 | 最低版本 | 说明 |
|---|---|---|
| Python | **3.11**（硬性要求，见 `pyproject.toml` 的 `requires-python = ">=3.11"`） | 禁止使用系统默认 3.9；类型注解使用 3.10+ union 语法 |
| PostgreSQL | 14+ | 异步驱动 `asyncpg` + 同步驱动 `psycopg2-binary` 并存 |
| Redis | 6+ | Celery Broker + Result Backend |
| FFmpeg | 4.4+ | 视频预处理（Feature-016）转码 / 分段 / 音频抽取 |
| GPU（可选） | CUDA 11.8+ | 启用 YOLOv8 姿态估计（`POSE_BACKEND=auto` 时首选）；未启用则自动降级到 MediaPipe CPU |

当前最新数据库迁移：`0015_kb_audit_and_expand_action_types`。

---

## 2. 系统级依赖（两条路径都必须先装）

```bash
# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y \
    build-essential \
    ffmpeg \
    libpq-dev \
    postgresql-client \
    redis-tools \
    git curl
```

> `libpq-dev` 是 `psycopg2-binary` 构建所需；`ffmpeg` 是 Feature-016 的硬依赖，不装会导致预处理 Worker 全量失败。

确认 FFmpeg 可用：

```bash
ffmpeg -version | head -1        # 期望 ≥ 4.4
```

确认 PostgreSQL 与 Redis 已运行（本机或远端均可）：

```bash
pg_isready -h <host> -p 5432     # 返回 accepting connections
redis-cli -h <host> -p 6379 ping # 返回 PONG
```

---

## 3. 路径 A：Conda（推荐，主路径）

项目统一约定解释器为 `/opt/conda/envs/coaching/bin/python3.11`，所有运行脚本、Celery Worker、测试命令都基于该路径。

### 3.1 创建 conda 环境

```bash
# 安装 Miniconda（若尚未安装）
curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash /tmp/miniconda.sh -b -p /opt/conda
export PATH=/opt/conda/bin:$PATH

# 创建名为 coaching 的 3.11 环境
conda create -y -n coaching python=3.11
```

### 3.2 安装项目依赖

```bash
cd /path/to/charhuang_pp_cn

# 运行态依赖
/opt/conda/envs/coaching/bin/pip install -e .

# 开发依赖（pytest / httpx / pytest-asyncio / pytest-cov）
/opt/conda/envs/coaching/bin/pip install -e ".[dev]"

# GPU 姿态估计（可选）
/opt/conda/envs/coaching/bin/pip install -e ".[gpu]"
```

### 3.3 冒烟校验

```bash
/opt/conda/envs/coaching/bin/python3.11 --version           # Python 3.11.x
/opt/conda/envs/coaching/bin/python3.11 -c "import fastapi, celery, sqlalchemy, whisper, mediapipe; print('ok')"
```

---

## 4. 路径 B：uv（加速路径，可选）

[uv](https://github.com/astral-sh/uv) 是 Rust 实现的高速 Python 包管理器，解析 + 安装通常比 `pip` 快 10 倍以上。适合 CI / 临时构建 / 本地快速验证。

**重要**：使用 uv 后，项目仍然按 "虚拟环境里跑一份 3.11 解释器" 的方式运作。把下文 `/opt/conda/envs/coaching/bin/python3.11` 全部替换为 `.venv/bin/python`，其他所有命令（uvicorn / celery / alembic / pytest）语义完全一致。

### 4.1 安装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version                # 建议 ≥ 0.4.0
```

### 4.2 创建虚拟环境 + 装依赖

```bash
cd /path/to/charhuang_pp_cn

# 让 uv 接管一个 3.11 解释器；若本机没有，uv 会自动下载
uv venv --python 3.11 .venv

# 运行态 + 开发依赖一次性装齐
uv pip install -e ".[dev]"

# GPU 可选依赖
uv pip install -e ".[gpu]"
```

### 4.3 冒烟校验

```bash
.venv/bin/python --version
.venv/bin/python -c "import fastapi, celery, sqlalchemy, whisper, mediapipe; print('ok')"
```

> 如果你在本机长期用 uv 替代 conda，建议在 shell profile 里加一行  
> `alias pycoach='/path/to/charhuang_pp_cn/.venv/bin/python'`  
> 以避免和项目文档中的 `/opt/conda/envs/coaching/bin/python3.11` 反复手动替换。

---

## 5. 配置 `.env`

复制模板并按实际凭证填充：

```bash
cp .env.example .env
```

`.env.example` 提供了全部必需字段（数据库 / Redis / COS / 临时目录 / APP_ENV）。至少需要替换：

| 字段 | 说明 |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://<user>:<pwd>@<host>:5432/<db>`（异步驱动，不能写成 `postgresql://`） |
| `REDIS_URL` | Celery Broker，需选一个空闲 db，避免和其他服务串线 |
| `COS_SECRET_ID` / `COS_SECRET_KEY` / `COS_BUCKET` / `COS_REGION` | 腾讯云 COS 凭证，**禁止硬编码到代码** |
| `TMP_DIR` | 视频预处理中转目录，需预留 ≥ 20GB 可用空间 |

此外按需追加：

```bash
# LLM：Venus Proxy 优先 → OpenAI fallback
VENUS_TOKEN=xxxxxxxx
OPENAI_API_KEY=xxxxxxxx

# COS 视频全量根路径（Feature-008 之后新功能统一使用）
COS_VIDEO_ALL_COCAH=charhuang/tt_video/乒乓球合集【较新】/

# 姿态估计后端
POSE_BACKEND=auto              # auto | yolov8 | mediapipe
```

---

## 6. 数据库初始化

```bash
# Conda 路径
/opt/conda/envs/coaching/bin/alembic upgrade head

# uv 路径
.venv/bin/alembic upgrade head
```

成功标志：命令无报错，且 `alembic_version` 表当前值为 `0015_kb_audit_and_expand_action_types`。

若迁移中途失败，修复后可安全重跑；Alembic 自身是幂等的，已应用版本不会重复执行。

---

## 7. 启动服务

项目章程规则 7 定义了一个 API + 五个 Celery Worker 的拓扑，一个队列一个 Worker，物理隔离。

> 以下命令默认使用 Conda 路径；uv 路径只需把 `/opt/conda/envs/coaching/bin/` 换成 `.venv/bin/`。

### 7.1 API（端口 8080，无热重载）

```bash
pkill -f "uvicorn src.api.main" 2>/dev/null
setsid /opt/conda/envs/coaching/bin/uvicorn src.api.main:app \
    --host 0.0.0.0 --port 8080 \
    >> /tmp/uvicorn.log 2>&1 &
```

### 7.2 五个 Celery Worker

```bash
# 1. 分类队列
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
    --loglevel=info --concurrency=1 -Q classification \
    -n classification_worker@%h >> /tmp/celery_classification_worker.log 2>&1 &

# 2. 知识库提取队列
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
    --loglevel=info --concurrency=2 -Q kb_extraction \
    -n kb_extraction_worker@%h >> /tmp/celery_kb_extraction_worker.log 2>&1 &

# 3. 运动员诊断队列
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
    --loglevel=info --concurrency=2 -Q diagnosis \
    -n diagnosis_worker@%h >> /tmp/celery_diagnosis_worker.log 2>&1 &

# 4. 默认队列（COS 扫描 + 清理）
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
    --loglevel=info --concurrency=1 -Q default \
    -n default_worker@%h >> /tmp/celery_default_worker.log 2>&1 &

# 5. 视频预处理队列（Feature-016）
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
    --loglevel=info --concurrency=3 -Q preprocessing \
    -n preprocessing_worker@%h >> /tmp/celery_preprocessing_worker.log 2>&1 &
```

> `.codebuddy/skills/system-init/restart_workers.sh` 已封装好上述五条，日常重启直接 `bash restart_workers.sh` 即可。

### 7.3 健康检查

```bash
curl -sS http://127.0.0.1:8080/api/v1/health | head -c 200
# 期望：{"success":true,"data":{"status":"ok", ... }}

# 检查 5 个 Worker 是否就绪
ps -ef | grep -E "celery .* worker" | grep -v grep | awk '{print $NF}'
# 期望能看到 5 个 -n xxx_worker@host
```

---

## 8. 跑测试

```bash
# Conda
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/unit/ -v               # 单元，无外部依赖
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/integration/ -v        # 集成，需 PG + Redis
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v                    # 全量

# uv
.venv/bin/python -m pytest tests/unit/ -v
```

---

## 9. 常见问题

| 现象 | 排查方向 |
|---|---|
| `ModuleNotFoundError: No module named 'src'` | 没跑 `pip install -e .`；或 pytest 不是用项目虚拟环境的解释器启动 |
| `asyncpg.InvalidCatalogNameError` | `DATABASE_URL` 指向的库尚未创建，先 `createdb coaching_db` |
| Celery 启动后任务一直 `PENDING` | 检查 `REDIS_URL` 是否和 API / Worker 三端完全一致；`redis-cli keys 'celery-*'` 观察是否入队 |
| 预处理任务卡 `running` 不推进 | 见章程"治本"条款：`preprocessing_task` 每次任务重建 engine；如仍卡，用 `system-init` skill 清库重跑 |
| `sqlalchemy.exc.InvalidRequestError: Async ... different loop` | Celery prefork 复用子进程导致；确保你跑的是最新代码（Commit `fc3b533` 之后） |
| ffmpeg 命令找不到 | `apt install ffmpeg`；在 docker 内注意基础镜像是否裁剪过 |

---

## 10. 一键脚本（可选）

如果你想在新机器上一把梭，以下是 Conda 路径的最小化脚本（保存为 `scripts/bootstrap.sh` 再按需调整）：

```bash
#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y build-essential ffmpeg libpq-dev postgresql-client redis-tools git curl

[[ -d /opt/conda ]] || {
  curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
  bash /tmp/miniconda.sh -b -p /opt/conda
}
export PATH=/opt/conda/bin:$PATH

conda env list | grep -q '^coaching ' || conda create -y -n coaching python=3.11

/opt/conda/envs/coaching/bin/pip install -e ".[dev]"

[[ -f .env ]] || cp .env.example .env
echo "请手动编辑 .env 填入真实凭证后，再执行："
echo "  /opt/conda/envs/coaching/bin/alembic upgrade head"
echo "  bash .codebuddy/skills/system-init/restart_workers.sh"
```

uv 路径等价脚本只需把最后 3 行的 pip / alembic 换成 `uv pip install -e ".[dev]"` 和 `.venv/bin/alembic upgrade head`。
