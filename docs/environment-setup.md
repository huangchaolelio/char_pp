# 环境安装指南

> 目标读者：第一次在全新机器上部署本项目的后端开发 / 联调 / 运维同学。
> 涵盖两条等价的安装路径：**Conda 主路径（推荐）** 与 **uv 加速路径**。两条路径产出的运行态完全一致，任选其一即可。
>
> 本指南在 **Debian/Ubuntu** 与 **TencentOS / RHEL / CentOS / RockyLinux**（`dnf` 系）上均已验证通过，关键步骤均给出了两套命令。

---

## 0. 10 分钟极速路径（TL;DR）

> 适用：**全新机器，一个命令块拷完收工**。前提：已装 `git / curl / sudo`，且当前用户可以 `sudo`。以下以 **TencentOS 4.4 + Conda 主路径** 为基准；Ubuntu 用户把 `dnf` 换成 `apt-get`、包名按 §2.2 对照表替换即可。

```bash
# === [1/7] 系统级依赖（ffmpeg / libpq / PG client / Redis client / 编译链） ===
sudo dnf install -y gcc gcc-c++ make ffmpeg libpq-devel postgresql redis-cli

# === [2/7] 安装 Miniconda 到 /opt/conda 并创建 coaching 环境 ===
sudo mkdir -p /opt/conda && sudo chown "$USER":"$(id -gn)" /opt/conda
curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash /tmp/miniconda.sh -b -u -p /opt/conda
# ⚠️ 新版 conda（≥24.x）强制先接受 Anaconda 频道 ToS，否则 conda create 会报错
/opt/conda/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
/opt/conda/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
/opt/conda/bin/conda create -y -n coaching python=3.11

# === [3/7] 装项目依赖（绕过 openai-whisper 的 pkg_resources 构建坑，见 §9） ===
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"   # 切到仓库根
echo 'setuptools<81' > /tmp/constraints.txt
PIP_CONSTRAINT=/tmp/constraints.txt \
    /opt/conda/envs/coaching/bin/pip install -e ".[dev]"

# === [4/7] 本机安装并启动 PostgreSQL + Redis 服务端（详见 §5.5） ===
sudo dnf install -y postgresql-server postgresql-contrib redis
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql redis
# 把 pg_hba.conf 的 host 行从 ident 改成 scram-sha-256（asyncpg 以 TCP 连 localhost 必须）
sudo sed -i 's/\bident\b/scram-sha-256/g' /var/lib/pgsql/data/pg_hba.conf
sudo systemctl reload postgresql
# 建库 + 设 postgres 密码（TJJ 环境里 `sudo -u postgres` 可能被策略拒，改用 `sudo bash -c "su - postgres -c '...'"`）
sudo bash -c "su - postgres -c \"psql -c \\\"ALTER USER postgres WITH PASSWORD 'password';\\\"\""
sudo bash -c "su - postgres -c 'createdb coaching_db'" || true

# === [5/7] 填 .env（至少 DATABASE_URL / REDIS_URL 两项指向本机） ===
[ -f .env ] || cp .env.example .env
# 确认：DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/coaching_db
#       REDIS_URL=redis://localhost:6379/0

# === [6/7] 数据库迁移 ===
/opt/conda/envs/coaching/bin/alembic -c alembic.ini upgrade head
# 期望最后一行：Running upgrade ... -> 0015_kb_audit_and_expand_action_types

# === [7/7] 启动 API + 开发模式一合一 Worker（5 队列合并，快速验证用） ===
nohup /opt/conda/envs/coaching/bin/uvicorn src.api.main:app \
    --host 0.0.0.0 --port 8080 > /tmp/uvicorn.log 2>&1 &
nohup /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app:celery_app worker \
    -Q classification,kb_extraction,diagnosis,preprocessing,default \
    --concurrency=2 --loglevel=INFO -n dev@%h > /tmp/celery_dev.log 2>&1 &
sleep 5 && curl -s http://127.0.0.1:8080/health     # 期望：{"status":"ok"}
```

⏱️ **预期总耗时**：首次全新机器 15–25 分钟（大头是 `openai-whisper` 拉 `torch` ~800MB）。

> 如果以上任何一步失败，**回到下面对应的小节精读**（每个命令都有出处和回滚策略）。

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

> ℹ️ 项目约定解释器绝对路径为 `/opt/conda/envs/coaching/bin/python3.11`。所有运行脚本、systemd unit、`.codebuddy/skills/system-init/restart_workers.sh` 都 hardcode 了该路径；若走 uv 路径，请统一替换为 `.venv/bin/python`。

---

## 2. 系统级依赖（两条路径都必须先装）

### 2.1 Debian / Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y \
    build-essential \
    ffmpeg \
    libpq-dev \
    postgresql-client \
    redis-tools \
    git curl
```

### 2.2 TencentOS / RHEL / CentOS / RockyLinux（`dnf` / `yum`）

```bash
sudo dnf install -y \
    gcc gcc-c++ make \
    ffmpeg \
    libpq-devel \
    postgresql \
    redis \
    git curl
```

📌 **apt → dnf 等价对照**：

| Debian/Ubuntu | TencentOS/RHEL |
|---|---|
| `build-essential` | `gcc gcc-c++ make`（或 `@development-tools` 组） |
| `libpq-dev` | `libpq-devel` |
| `postgresql-client` | `postgresql`（只含 `psql`/`pg_isready`，不含服务端） |
| `redis-tools` | `redis`（在 TencentOS 上**二进制包就叫 `redis`**，同时包含 client + server） |

### 2.3 版本验证

```bash
ffmpeg -version | head -1        # 期望 ≥ 4.4
pg_isready --version             # 期望 ≥ 14
redis-cli --version              # 期望 ≥ 6
```

### 2.4 PG / Redis 运行态连通性

```bash
pg_isready -h <host> -p 5432     # 期望：accepting connections
redis-cli -h <host> -p 6379 ping # 期望：PONG
```

> 本机没有 PG / Redis 服务端？跳到 **§5.5 本机安装 PostgreSQL + Redis 服务端**。

---

## 3. 路径 A：Conda（推荐，主路径）

项目统一约定解释器为 `/opt/conda/envs/coaching/bin/python3.11`，所有运行脚本、Celery Worker、测试命令都基于该路径。

### 3.1 安装 Miniconda 到 `/opt/conda`

```bash
# 事先确保 /opt/conda 目录归当前用户所有（避免后续 pip install 需要 sudo）
sudo mkdir -p /opt/conda && sudo chown "$USER":"$(id -gn)" /opt/conda

# 下载并静默安装（`-u` 允许覆盖已存在目录）
curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash /tmp/miniconda.sh -b -u -p /opt/conda
```

### 3.2 接受 Anaconda 频道 ToS（**新版 conda 必做**）

新版 conda（≥ 24.x）启用了 Terms of Service 门槛，**不接受就无法安装任何官方频道的包**，会报 `CondaToSNonInteractiveError`。一次性接受即可：

```bash
/opt/conda/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
/opt/conda/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
```

### 3.3 创建 `coaching` 环境

```bash
/opt/conda/bin/conda create -y -n coaching python=3.11
/opt/conda/envs/coaching/bin/python3.11 --version        # 期望 Python 3.11.x
```

### 3.4 把 conda init 写入 shell（可选但强烈建议）

```bash
/opt/conda/bin/conda init bash        # 写入 ~/.bashrc
# 对 zsh/fish 同理：conda init zsh / conda init fish
# 若是登录 shell 也要生效（某些远程/SSH 场景），建议在 ~/.bash_profile 里追加：
grep -q '. ~/.bashrc' ~/.bash_profile 2>/dev/null || \
    printf 'if [ -f ~/.bashrc ]; then\n    . ~/.bashrc\nfi\n' >> ~/.bash_profile
```

### 3.5 安装项目依赖

```bash
cd "$(git rev-parse --show-toplevel)"   # 项目根目录

# ⚠️ 规避 openai-whisper==20231117 的 `pkg_resources` 构建坑（详见 §9）
echo 'setuptools<81' > /tmp/constraints.txt
export PIP_CONSTRAINT=/tmp/constraints.txt

# 运行态 + 开发依赖（推荐一次装齐）
/opt/conda/envs/coaching/bin/pip install -e ".[dev]"

# GPU 姿态估计（可选）
/opt/conda/envs/coaching/bin/pip install -e ".[gpu]"
```

> ⏱️ 首次安装预计 **10–15 分钟**：`openai-whisper` 会拉 `torch`（~800MB），`mediapipe` / `opencv` 合计 ~150MB。国内网络可提前 `pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple`。

### 3.6 冒烟校验

```bash
/opt/conda/envs/coaching/bin/python3.11 --version           # Python 3.11.x
/opt/conda/envs/coaching/bin/python3.11 -c "import fastapi, celery, sqlalchemy, whisper, mediapipe; print('ok')"
/opt/conda/envs/coaching/bin/pip show charhuang-pp-cn | head -3    # editable 安装存在
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
cd "$(git rev-parse --show-toplevel)"

# 让 uv 接管一个 3.11 解释器；若本机没有，uv 会自动下载
uv venv --python 3.11 .venv

# 同样要规避 whisper 的 pkg_resources 构建坑
echo 'setuptools<81' > /tmp/constraints.txt
export PIP_CONSTRAINT=/tmp/constraints.txt

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

## 5.5 本机安装 PostgreSQL + Redis 服务端（localhost 部署）

如果你打算把 PG / Redis 跑在本机（`.env` 里配 `localhost`），按以下流程；否则跳过。

### 5.5.1 安装并启动服务

**Debian/Ubuntu**：
```bash
sudo apt-get install -y postgresql postgresql-contrib redis-server
sudo systemctl enable --now postgresql redis-server
```

**TencentOS/RHEL**：
```bash
sudo dnf install -y postgresql-server postgresql-contrib redis
sudo postgresql-setup --initdb        # 首次必须执行，初始化 /var/lib/pgsql/data
sudo systemctl enable --now postgresql redis
```

> ⚠️ **TencentOS 专属提醒**：`redis` 包在系统里可能**已装但未启**（`rpm -q redis` 显示已装，但 `systemctl is-active redis` 返回 `inactive`）。直接 `systemctl enable --now redis` 即可，无需重装。**不要**装 `valkey` / `valkey-compat-redis`——它们会与 `redis < 7.4` 冲突并触发 `dnf install` 失败。

### 5.5.2 改 `pg_hba.conf` 让 TCP 密码认证生效

`postgresql-setup --initdb` 的默认 `pg_hba.conf` 对 `127.0.0.1` / `::1` 使用 `ident` 认证（按系统用户名匹配），导致：

```
asyncpg.exceptions.InvalidAuthorizationSpecificationError: password authentication failed for user "postgres"
```

因为 `.env` 里的驱动是 `postgresql+asyncpg://postgres:password@localhost:5432/...`，asyncpg 走 TCP 且带密码。**必须**改成 `scram-sha-256`（或 `md5`）：

```bash
# 备份 + 一键替换所有 host 行的 ident
sudo cp -n /var/lib/pgsql/data/pg_hba.conf /var/lib/pgsql/data/pg_hba.conf.bak
sudo sed -i 's/\bident\b/scram-sha-256/g' /var/lib/pgsql/data/pg_hba.conf
sudo grep -E '^host' /var/lib/pgsql/data/pg_hba.conf
# 期望 127.0.0.1/32 和 ::1/128 两行都是 scram-sha-256
sudo systemctl reload postgresql
```

> Debian/Ubuntu 的 `pg_hba.conf` 在 `/etc/postgresql/<version>/main/pg_hba.conf`；默认是 `md5` / `peer`，一般无需改。

### 5.5.3 设置 postgres 密码 + 创建 `coaching_db`

⚠️ **腾讯内网 TJJ 策略**下，`sudo -u postgres <cmd>` 会被拒：
```
Sorry, user <你> is not allowed to execute '/usr/bin/psql ...' as postgres on <host>
```
解决方式是先 `sudo` 到 root，再 `su - postgres`：

```bash
# 设密码
sudo bash -c "su - postgres -c \"psql -c \\\"ALTER USER postgres WITH PASSWORD 'password';\\\"\""

# 建库（若已存在会报错，可忽略）
sudo bash -c "su - postgres -c 'createdb coaching_db'" || true

# 验证
sudo bash -c "su - postgres -c 'psql -l'" | grep coaching_db
```

### 5.5.4 从应用端验证连通性

```bash
/opt/conda/envs/coaching/bin/python3.11 - <<'PY'
import asyncio, asyncpg, redis
async def pg():
    c = await asyncpg.connect("postgresql://postgres:password@localhost:5432/coaching_db")
    print("[PG OK]", (await c.fetchval("SELECT version();"))[:60])
    await c.close()
asyncio.run(pg())
print("[Redis OK]", redis.Redis(host="localhost", port=6379, db=0).ping())
PY
```

期望输出：
```
[PG OK] PostgreSQL 15.x on x86_64-...
[Redis OK] True
```

---

## 6. 数据库初始化

```bash
# Conda 路径
/opt/conda/envs/coaching/bin/alembic -c alembic.ini upgrade head

# uv 路径
.venv/bin/alembic -c alembic.ini upgrade head
```

成功标志：命令无报错，且 `alembic_version` 表当前值为 `0015_kb_audit_and_expand_action_types`；`\dt` 能看到 27 张表（含 `analysis_tasks` / `coaches` / `extraction_jobs` 等）。

若迁移中途失败，修复后可安全重跑；Alembic 自身是幂等的，已应用版本不会重复执行。

---

## 7. 启动服务

项目章程规则 7 定义了一个 API + 五个 Celery Worker + 一个 Celery Beat 的拓扑，一个队列一个 Worker，物理隔离；Beat 独占一个进程，**必须**启动——否则所有周期任务（每日 `cleanup_expired_tasks`、每小时 `cleanup_intermediate_artifacts`、每 5 分钟 `sweep_orphan_jobs`）都不会触发，僵尸 job 会卡死 kb_extraction 通道无法自愈。生产环境按 §7.2 部署；本地开发 / 快速冒烟可用 §7.3 的"一合一"形态。

> 以下命令默认使用 Conda 路径；uv 路径只需把 `/opt/conda/envs/coaching/bin/` 换成 `.venv/bin/`。

### 7.1 API（端口 8080，无热重载）

```bash
pkill -f "uvicorn src.api.main" 2>/dev/null
nohup /opt/conda/envs/coaching/bin/uvicorn src.api.main:app \
    --host 0.0.0.0 --port 8080 \
    >> /tmp/uvicorn.log 2>&1 &
```

### 7.2 生产形态：五个 Celery Worker + 一个 Celery Beat（每队列一个进程）

```bash
# 1. 分类队列
nohup /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app:celery_app worker \
    --loglevel=info --concurrency=1 -Q classification \
    -n classification_worker@%h >> /tmp/celery_classification_worker.log 2>&1 &

# 2. 知识库提取队列
nohup /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app:celery_app worker \
    --loglevel=info --concurrency=2 -Q kb_extraction \
    -n kb_extraction_worker@%h >> /tmp/celery_kb_extraction_worker.log 2>&1 &

# 3. 运动员诊断队列
nohup /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app:celery_app worker \
    --loglevel=info --concurrency=2 -Q diagnosis \
    -n diagnosis_worker@%h >> /tmp/celery_diagnosis_worker.log 2>&1 &

# 4. 默认队列（COS 扫描 + 清理 + orphan sweep）
nohup /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app:celery_app worker \
    --loglevel=info --concurrency=1 -Q default \
    -n default_worker@%h >> /tmp/celery_default_worker.log 2>&1 &

# 5. 视频预处理队列（Feature-016）
nohup /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app:celery_app worker \
    --loglevel=info --concurrency=3 -Q preprocessing \
    -n preprocessing_worker@%h >> /tmp/celery_preprocessing_worker.log 2>&1 &

# 6. Celery Beat（定时调度器，全局唯一，不带 worker/-Q 参数）
#    驱动 cleanup_expired_tasks（每日）/ cleanup_intermediate_artifacts（每小时）/
#    sweep_orphan_jobs（每 5 分钟，回收 OOM / WorkerLostError 卡住的 running 任务）。
#    调度状态持久化到 /tmp/celerybeat-schedule（重启自动续跑）。
#    ⚠️ 全集群只能启 1 个 beat，起多个会重复派发周期任务。
nohup /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app:celery_app beat \
    --loglevel=info --schedule=/tmp/celerybeat-schedule \
    >> /tmp/celery_beat.log 2>&1 &
```

> `.codebuddy/skills/system-init/restart_workers.sh` 已封装好上述六条，日常重启直接 `bash restart_workers.sh` 即可。

### 7.3 开发模式：单 Worker 合并 5 队列（快速验证）

只想拉起整个系统跑个 smoke test？不需要起 5 个进程，一个 worker 监听全部队列就行：

```bash
nohup /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app:celery_app worker \
    -Q classification,kb_extraction,diagnosis,preprocessing,default \
    --concurrency=2 --loglevel=INFO -n dev@%h \
    >> /tmp/celery_dev.log 2>&1 &
```

> ⚠️ 仅限本地开发。生产**必须**走 §7.2，否则 `classification` 的慢任务会挤占 `default` 的定时清理；`task_acks_late=True` + 单 worker 共享并发池遇到 OOM 会全队列一起死。

### 7.4 健康检查

```bash
# API 存活
curl -s http://127.0.0.1:8080/health
# 期望：{"status":"ok"}

# API 详细状态（若实现）
curl -s http://127.0.0.1:8080/api/v1/health | head -c 200

# 检查 Worker 是否就绪
/opt/conda/envs/coaching/bin/celery -A src.workers.celery_app:celery_app inspect ping
# 期望：-> <worker_name>: OK    pong

# 生产 5-worker 形态下，worker 父进程数应 == 5
ps -ef | grep -E "celery .* worker" | grep -v grep | awk '{print $NF}' | sort -u

# 检查 Beat 是否在跑（有且仅有 1 个进程）；无此进程则所有周期任务都不会触发
pgrep -af "celery.*celery_app.*beat" || echo "[WARN] celery beat 未启动"

# 查看 Beat 最近调度日志（应能看到 "Scheduler: Sending due task sweep-orphan-jobs" 等）
tail -n 30 /tmp/celery_beat.log
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
| `asyncpg.InvalidCatalogNameError` | `DATABASE_URL` 指向的库尚未创建，先 `createdb coaching_db`（见 §5.5.3） |
| `asyncpg...password authentication failed for user "postgres"` | `pg_hba.conf` 的 `host` 行还是 `ident`/`peer`，要改 `scram-sha-256`（见 §5.5.2） |
| `Connection refused` 连 5432/6379 | 服务未启动：`sudo systemctl enable --now postgresql redis` |
| `CondaToSNonInteractiveError` 安装 conda 包时 | 接受频道 ToS（见 §3.2） |
| pip 装依赖时 `ModuleNotFoundError: No module named 'pkg_resources'`（构建 `openai-whisper==20231117`） | 新版 setuptools (≥81) 移除了 `pkg_resources`；用 `PIP_CONSTRAINT` 把 setuptools 钉到 <81，或 `--no-build-isolation` 重装（见 §3.5） |
| `Sorry, user xxx is not allowed to execute 'psql' as postgres` (TJJ 内网) | 改用 `sudo bash -c "su - postgres -c '...'"`（见 §5.5.3） |
| `dnf install valkey-compat-redis` 报 `conflicts with redis < 7.4` | 系统已装 `redis-7.2.7`，**不要装 valkey**，直接 `systemctl enable --now redis` |
| Celery 启动后任务一直 `PENDING` | 检查 `REDIS_URL` 是否和 API / Worker 三端完全一致；`redis-cli keys 'celery-*'` 观察是否入队 |
| Celery worker 启动即报 `ImportError` 或 `RuntimeError: Event loop is closed` | 通常是 asyncpg + prefork 的 fork-safety 问题；确保你跑的是最新代码（Commit `fc3b533` 之后），`worker_process_init` 钩子会重建 engine |
| 预处理任务卡 `running` 不推进 | 见章程"治本"条款：`preprocessing_task` 每次任务重建 engine；如仍卡，用 `system-init` skill 清库重跑 |
| `sqlalchemy.exc.InvalidRequestError: Async ... different loop` | Celery prefork 复用子进程导致；确保你跑的是最新代码（Commit `fc3b533` 之后） |
| ffmpeg 命令找不到 | `apt install ffmpeg` / `dnf install ffmpeg`；docker 基础镜像要确认没被裁剪 |
| `curl http://127.0.0.1:8080/api/v1/health` 返回 404 | 当前 API 的健康检查路径是 `/health`（非 `/api/v1/health`），看 `src/api/main.py` 的 router 注册确认 |

---

## 10. 一键脚本（可选）

如果你想在新机器上一把梭，以下是 **Conda + TencentOS** 路径的最小化脚本（保存为 `scripts/bootstrap.sh` 再按需调整）：

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1) 系统依赖
if command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y gcc gcc-c++ make ffmpeg libpq-devel postgresql redis git curl
else
    sudo apt-get update
    sudo apt-get install -y build-essential ffmpeg libpq-dev postgresql-client redis-tools git curl
fi

# 2) Miniconda
if [[ ! -x /opt/conda/bin/conda ]]; then
    sudo mkdir -p /opt/conda && sudo chown "$USER":"$(id -gn)" /opt/conda
    curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
    bash /tmp/miniconda.sh -b -u -p /opt/conda
fi
/opt/conda/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main || true
/opt/conda/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r || true
/opt/conda/bin/conda env list | grep -q '^coaching ' || /opt/conda/bin/conda create -y -n coaching python=3.11

# 3) 项目依赖
echo 'setuptools<81' > /tmp/constraints.txt
PIP_CONSTRAINT=/tmp/constraints.txt /opt/conda/envs/coaching/bin/pip install -e ".[dev]"

# 4) .env
[[ -f .env ]] || cp .env.example .env

cat <<'EOF'
==========================================================
后续手动步骤（取决于本机还是远端 PG/Redis）：
  • 本机：参考 §5.5 安装并配置 postgresql-server / redis 服务端
  • 远端：在 .env 里改 DATABASE_URL / REDIS_URL 到远端地址

然后：
  /opt/conda/envs/coaching/bin/alembic -c alembic.ini upgrade head
  bash .codebuddy/skills/system-init/restart_workers.sh
  curl http://127.0.0.1:8080/health
==========================================================
EOF
```

uv 路径等价脚本只需把最后 3 行的 pip / alembic 换成 `uv pip install -e ".[dev]"` 和 `.venv/bin/alembic upgrade head`。
