# 研究: 知识库提取流水线化 (Feature-014)

**阶段**: 0 — 大纲与研究
**日期**: 2026-04-24

## 研究目标

spec.md 与 plan.md 中无 NEEDS CLARIFICATION 标记（两轮 clarify 已全部解决）。本研究围绕**技术选型**与**最佳实践**，为阶段 1 设计提供决策依据。

---

## R1. DAG 编排机制：自建 vs 通用 DAG 引擎

### Decision: 自建轻量编排器（`src/services/kb_extraction_pipeline/orchestrator.py`）

### Rationale

- DAG 是**静态固定** 6 个子任务，不存在用户自定义 DAG 需求 → 不需要 Airflow/Prefect/Dagster 的表达力
- 通用 DAG 引擎引入：第二套调度器、独立元数据存储、新监控栈 → 违反章程 IV（YAGNI）
- 复用 Feature-013 Celery `kb_extraction` 队列作为作业级入队通道，作业内用 `asyncio.gather` 并行子任务 → 零新增基础设施
- 子任务状态持久化在 PostgreSQL `pipeline_steps` 表，查询直接 JOIN → 符合「DB 是事实来源」原则（Feature-013 已有基调）

### Alternatives Considered

| 方案 | 优势 | 拒绝原因 |
|------|------|---------|
| Airflow | 成熟、UI | 重量级（~200MB 镜像 + Postgres 元数据库），引入第二个调度器 |
| Prefect 2.x | Pythonic、无额外基础设施 | 引入新 Worker runtime，与 Celery 并存增加心智成本 |
| Dagster | 强类型 assets | 同 Prefect，过度设计 |
| Celery Canvas（`chain` + `group`） | Celery 原生 | 作业内部每子任务单独占 Celery slot → 与 Clarification Q1 冲突 |
| **自建 orchestrator + asyncio.gather** ✅ | 零依赖、DB 事实来源、直接兼容 Feature-013 | 无明显劣势 |

### Implementation Notes

- 调度器入口：Celery `kb_extraction_task.extract_kb` 接收作业 ID → 调用 `Orchestrator.run(job_id)`
- `Orchestrator.run` 内部以拓扑顺序循环；每轮启动所有**依赖已满足且处于 pending** 的子任务，用 `asyncio.gather(*tasks)` 并行等待
- 单子任务失败 → 标记 failed；下游通过广度优先传播标记 skipped → 作业整体 failed
- 重跑：仅重置 failed + 下游 skipped 的子任务状态为 pending，重走一遍拓扑循环

---

## R2. 作业内并行模型：asyncio.gather vs ThreadPool vs ProcessPool

### Decision: `asyncio.gather` + 按子任务类型选阻塞执行器

### Rationale

**子任务执行特征分类**：

| 子任务 | 阻塞类型 | 执行方式 |
|--------|----------|---------|
| download_video | I/O（COS SDK 同步调用） | `asyncio.to_thread()` 包装 |
| pose_analysis | CPU（YOLOv8/MediaPipe） | `asyncio.to_thread()` 包装 |
| audio_transcription | CPU（Whisper 推理） | `asyncio.to_thread()` 包装 |
| visual_kb_extract | CPU + 轻 I/O（规则 + 可选 LLM） | `asyncio.to_thread()` 包装 |
| audio_kb_extract | I/O（LLM HTTP 调用） | 原生异步 `httpx.AsyncClient` |
| merge_kb | DB（async SQLAlchemy） | 原生异步 |

- `asyncio.gather` 天然支持「等待所有前置完成」的依赖语义
- CPU 密集型用 `asyncio.to_thread` 放到默认线程池 → 不阻塞事件循环
- 与 Feature-013 `TaskSubmissionService` 同一事件循环模型，无新概念

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| ThreadPoolExecutor 显式管理 | 需手动实现 future 收集 + 超时，重复 asyncio 已有功能 |
| ProcessPoolExecutor | 姿态/Whisper 模型加载一次的缓存在子进程不共享，冷启动成本高 |
| 多个独立 Celery 子任务 | 违反 Clarification Q1（作业内并行不能占 concurrency 预算） |

---

## R3. 中间结果存储：本地 FS + DB 结构化字段混合

### Decision

| 数据类型 | 存储位置 | 理由 |
|---------|----------|------|
| 视频文件（50MB-2GB） | Worker 本地 `/tmp/coaching-advisor/jobs/{job_id}/video.mp4` | 二进制大对象，不适合 DB |
| 姿态关键点序列（~5-50MB JSON） | 本地 `/tmp/coaching-advisor/jobs/{job_id}/pose.json` | 大对象 + 仅流水线内部使用 |
| 音频转写（~50KB-500KB JSON） | 本地 `/tmp/coaching-advisor/jobs/{job_id}/transcript.json` | 同上 |
| 子任务状态 / 错误信息 / 耗时 | DB `pipeline_steps` 表 | 查询频繁、需结构化 |
| 中间知识条目草稿（视觉/音频路各自） | DB JSONB 字段 `pipeline_steps.output_summary` | 小数据 + 需支持重跑时读取 |
| 最终知识条目 | DB `tech_knowledge_bases` 表（已有）| 业务主数据 |
| 冲突项 | DB `kb_conflicts` 新表 | 审核流查询 |

### Rationale

- PostgreSQL 不擅长存储 50MB+ 二进制（bloat 严重）
- 但 Worker 本地 FS 不跨节点 → 作业必须绑定到同一 Worker（Celery 任务亲和性）
- Feature-013 `kb_extraction` 通道 concurrency=2 意味着同一 Worker 上最多 2 个并行作业 → 磁盘上限 ~5-10GB 可控
- 清理：作业 success 24h / failed 7d 后由 `housekeeping_task` 删除本地目录 + DB 中间字段

### Worker 亲和性实现

- `ExtractionJob.worker_hostname` 字段在 orchestrator 启动时写入 `socket.gethostname()`
- 重跑任务通过 Celery `queue` 参数路由到相同 Worker（需加自定义路由 `hostname=...`）或接受从零重跑（超保留期场景已在 edge case 处理）

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| 全部存本地 FS（含状态） | 无法跨进程/重启恢复作业状态 |
| 全部存 DB（含视频 + 姿态 JSON） | DB bloat、备份成本高、性能下降 |
| 新增 Redis/MinIO 作为中间存储 | 新增基础设施，违反 YAGNI |

---

## R4. 超时实现：Python 级 vs Celery `soft_time_limit`

### Decision

- **作业级超时 45 分钟**：在 `Orchestrator.run` 顶层用 `asyncio.wait_for(all_subtasks, timeout=2700)` 控制
- **子任务级超时 10 分钟**：每个 `step_executor.execute()` 内部用 `asyncio.wait_for(actual_work, timeout=600)`
- **Celery 兜底**：`@shared_task(soft_time_limit=2800, time_limit=2820)` 在作业级超时外加 100s 冗余，确保孤儿任务最终被 celery 回收

### Rationale

- `asyncio.wait_for` 在业务层面精确控制；Celery 超时作为最后防线
- 超时触发后 orchestrator 自行标记作业/子任务 failed（与 Q4 孤儿恢复语义一致），不依赖 Celery 孤儿 sweep

---

## R5. I/O 重试策略实现：tenacity vs Celery retry vs 手写装饰器

### Decision: `tenacity` 在子任务 executor 内部实现，不使用 Celery 任务级 retry

### Rationale

- Celery 的 `self.retry(exc, countdown=30)` 会**整个 Celery 任务**重入队，作业上下文丢失（重新走 orchestrator 冷启动）
- 子任务级重试应该是「调用点」级别的（只重试失败的 HTTP/COS 调用），不能让前置步骤也重跑
- `tenacity` 成熟库，已在 Python 生态广泛采用，无额外依赖成本
- 仅 I/O executor 引入：`download_video`、`audio_transcription`（LLM 调用部分）、`audio_kb_extract`

```python
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),  # 首次 + 2 次重试
    wait=wait_fixed(30),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OpenAIError)),
    reraise=True,
)
async def _download(...): ...
```

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| Celery `self.retry` | 上下文丢失、作业重启、不符合「只重试失败点」语义 |
| 手写 for 循环 | 重复造轮子 |
| `backoff` 库 | 与 `tenacity` 功能等价，项目无先例，不引入第二个库 |

---

## R6. 冲突标注表设计：参数粒度 vs 维度粒度

### Decision: 维度粒度 — 一个维度的视觉/音频冲突对应一行

### Rationale

- 审核员的心智模型是「这个维度取视觉的 105° 还是音频的 130°」，不是「这个参数的 min 字段取谁」
- 维度粒度表行数少（每个作业最多 10-30 行），便于列表 UI 展示
- superseded_by_job_id 字段在维度粒度下语义明确（「同一个维度，新作业有新结论了」）

### Schema Draft

```sql
CREATE TABLE kb_conflicts (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES extraction_jobs(id),
    cos_object_key VARCHAR(512) NOT NULL,
    tech_category VARCHAR(50) NOT NULL,
    dimension_name VARCHAR(200) NOT NULL,  -- "肘部角度", "重心偏移" 等
    visual_value JSONB,                     -- {"min": 90, "ideal": 105, "max": 120}
    audio_value JSONB,                      -- {"text": "肘部保持 130 度左右"}
    visual_confidence FLOAT,
    audio_confidence FLOAT,
    superseded_by_job_id UUID REFERENCES extraction_jobs(id),  -- 被 force 覆盖时填
    resolved_at TIMESTAMPTZ,                -- 审核完成时间（为未来审核预留，本 Feature 只写 NULL）
    resolved_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_kb_conflicts_pending ON kb_conflicts(cos_object_key)
    WHERE resolved_at IS NULL AND superseded_by_job_id IS NULL;
```

---

## R7. 作业 DAG 编排状态机

### Decision: 4 状态作业 + 5 状态子任务

**作业状态**:
- `pending` — 已提交，未开始
- `running` — 至少一个子任务在跑或已跑过
- `success` — 所有子任务 success（或音频路 skipped 走降级成功）
- `failed` — 任一关键子任务失败（导致合并子任务无法进行）

**子任务状态**:
- `pending` — 未开始
- `running` — 执行中
- `success` — 成功
- `failed` — 失败（含重试用尽 + 超时 + 孤儿）
- `skipped` — 上游失败导致跳过 / 音频路在无音频场景主动 skip

### 降级规则（FR-012）

- 音频路失败 → `merge_kb` 仍执行，只合入视觉路条目；整作业标 `success`
- 视觉路失败 → `merge_kb` 无法执行（因为 pose_analysis 是 `visual_kb_extract` 的唯一输入来源），作业标 `failed`
- 设计选择：视觉路是**关键路径**，音频路是**增强**

---

## R8. LLM 抽取调用：Venus 优先 / OpenAI 降级

### Decision: 沿用 `src/services/llm_client.py`（Feature-002 已有）

### Rationale

- 项目章程要求 Venus Proxy 优先 → OpenAI 降级
- 现有 `LLMClient` 已封装两者统一接口
- 本 Feature 的 `audio_kb_extract` 调用与 `visual_kb_extract` 调用都复用该 client
- 重试策略由 `tenacity` 在 `step_executor` 层统一处理；LLM client 内部无重试（避免双重重试）

---

## R9. 旧版 Feature-002 `extract_knowledge` 复用范围

### Decision: 函数级别切分复用，不继承旧 Celery 任务外壳

在 Feature-002 的 `src/services/kb_extractor.py`（如存在）或内嵌在旧 `expert_video_task.py`（已删除，但逻辑可从 git history 恢复）中：

| 旧逻辑 | 新归属 |
|--------|--------|
| 姿态关键点提取（yolov8_client / mediapipe_client） | `pose_analysis.py` 复用 |
| Whisper 转写 + 语言检测 | `audio_transcription.py` 复用 |
| 姿态序列 → 技术维度规则（角度、速度窗口） | `visual_kb_extract.py` 复用算法 |
| LLM 抽取教学要点 | `audio_kb_extract.py` 复用 prompt + LLM client |
| 知识条目合并 + 版本落库 | `merge_kb.py` 改造：新增冲突检测 + 冲突表写入 |

### Action

Phase 1 阶段需要从 git history (`git show 245d1da~1:src/workers/expert_video_task.py`) 取回旧实现作为参考，**不直接恢复旧文件**（会破坏 Feature-013 已删除它的设计）。

---

## R10. API 风格：与 Feature-013 `/api/v1/tasks/*` 的兼容

### Decision: 新增 `/api/v1/extraction-jobs` 命名空间，与 Feature-013 `/tasks/kb-extraction` 并存

### Rationale

- Feature-013 的 `POST /api/v1/tasks/kb-extraction` 作为「入口」保留，内部调用 Feature-014 的 orchestrator 创建 job + 子任务
- 新增 `GET /api/v1/extraction-jobs/{job_id}` 暴露作业内部细节（子任务清单、DAG 状态）
- 语义分层：
  - `/tasks/kb-extraction`：Feature-013 世界观（task_type 三类）
  - `/extraction-jobs`：Feature-014 世界观（作业 + 子任务 DAG）
- 一个作业同时持有 `analysis_task_id`（指向 `analysis_tasks` 行，给 Feature-013 通道/限流用）和 `job_id`（本 Feature 内部）

---

## 汇总

| 决策 | 文档节点 |
|------|---------|
| 自建 DAG 编排 + asyncio.gather | R1, R2 |
| 本地 FS + DB 结构化字段混合存储 | R3 |
| `asyncio.wait_for` 双层超时 + Celery 兜底 | R4 |
| `tenacity` 在子任务 executor 内部重试 | R5 |
| 冲突表维度粒度 | R6 |
| 4/5 状态机 + 降级规则（音频可失败不拖作业） | R7 |
| 复用 `LLMClient`（Venus → OpenAI） | R8 |
| 从 git history 恢复 Feature-002 算法逻辑到新 executor 结构 | R9 |
| 分层 API 命名（`/tasks/kb-extraction` + `/extraction-jobs`） | R10 |

**所有决策均无 NEEDS CLARIFICATION 残留。阶段 1 可以开始。**
