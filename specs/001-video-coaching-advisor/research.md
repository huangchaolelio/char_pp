# 研究报告: 视频教学分析与专业指导建议

**功能分支**: `001-video-coaching-advisor`
**日期**: 2026-04-17
**状态**: 完成

## 1. 姿态估计框架选型

### Decision: MediaPipe Pose（Google）

**Rationale**:
- 提供 33 个全身关键点，覆盖乒乓球动作分析所需的肩、肘、腕、髋、膝等关键节点
- 支持 Python 原生调用，无需 GPU 即可在 CPU 上以 >30fps 推理标准分辨率视频
- 推理延迟低（单帧 <30ms on CPU），适合批量视频处理场景
- Apache 2.0 许可证，无商业使用限制
- 提供置信度分数（visibility score）用于关键点可靠性判断，直接支持 FR-005 置信度需求

**Alternatives considered**:
- **OpenPose**：精度高但推理速度慢（需 GPU），依赖复杂，非商业许可证限制多；不选
- **MMPose**：精度最高，支持多种模型，但工程集成复杂度高，依赖 PyTorch 生态，
  启动成本大；作为 v2 精度提升备选

**输入质量门控参数（基于 MediaPipe 推荐值）**:
- 最低帧率：**15 fps**（低于此值关键点时序分析不可靠）
- 最低分辨率：**480p（854×480）**（低于此值关键点检测误差显著增大）
- 关键点 visibility 阈值：**0.5**（低于此值的关键点视为不可靠，触发遮挡判断）
- 整体置信度阈值：**0.7**（对应 FR-005，基于各关键点 visibility 加权平均）

---

## 2. 视频处理方案

### Decision: OpenCV + FFmpeg（ffmpeg-python 封装）

**Rationale**:
- OpenCV 提供逐帧读取、关键帧提取、图像预处理能力，与 MediaPipe 无缝集成
- FFmpeg 负责视频格式转换和元数据提取（帧率、分辨率、时长），支持 MP4/AVI/MOV 主流格式
- 两者均为成熟开源方案，社区资料丰富

**视频输入规格**:
- 支持格式：MP4（H.264）、AVI、MOV
- 最大文件大小：**2GB**（单段视频，基于 5 分钟 1080p 的典型大小上限）
- 帧提取策略：每秒提取 **15 帧**（对 >15fps 视频进行均匀降采样），平衡精度与处理速度

---

## 3. 异步任务队列

### Decision: Celery + Redis

**Rationale**:
- 规范要求单段视频（≤5 分钟）在提交后 5 分钟内完成分析（SC-004，近实时异步）
- Celery 支持任务状态追踪（PENDING → STARTED → SUCCESS/FAILURE），满足 FR-009 审计需求
- Redis 作为 Broker 和结果后端，延迟低（<1ms），适合中等并发场景
- 任务状态可通过 task_id 轮询或 Webhook 回调通知调用方

**任务超时配置**:
- 单段视频分析任务超时：**8 分钟**（留 3 分钟余量，超时后返回 TIMEOUT 状态）
- 最大重试次数：**2 次**（重试间隔指数退避）

**Alternatives considered**:
- **RQ（Redis Queue）**：轻量但功能有限，缺乏任务状态追踪细粒度控制；不选
- **Celery + RabbitMQ**：RabbitMQ 运维复杂度高于 Redis，本项目并发规模不需要 RabbitMQ 的高级特性；不选

---

## 4. 存储方案

### Decision: PostgreSQL（结构化数据）+ 腾讯云 COS（专家教练视频源）+ 本地临时目录（处理中间文件）

**Rationale**:
- **PostgreSQL** 存储所有结构化数据：知识库、偏差报告、指导建议、分析任务元数据
  - JSONB 字段支持技术要点参数的灵活结构（不同动作类型维度数不同）
  - 版本化知识库通过 version 字段 + 不可变记录实现（不删除，仅新增版本）
  - 12 个月数据保留通过定时任务 + 软删除标记实现（用户主动删除置 deleted_at）
- **腾讯云 COS（专家教练视频）**: 专业教练教学视频由管理员预先上传至 COS；
  调用方提交分析任务时只需传入 COS Object Key（如 `coach-videos/forehand_lesson_001.mp4`），
  服务端通过 `cos-python-sdk-v5` 将视频下载到本地临时目录后处理，处理完成立即清除临时文件。
  COS 中的教练视频长期保留（非个人数据，不受 12 个月限制），供知识库迭代复用。
- **本地临时目录**（`/tmp/coaching-advisor/`）：视频下载和处理的中间文件，任务完成或失败
  后统一清理，不持久化视频内容。
- **运动员视频**：通过 API 直接上传（multipart），同样只保留在本地临时目录处理，
  处理完成后清除，不持久化到任何存储，仅保留分析结果元数据。

**COS 访问配置**（通过环境变量注入，不硬编码）:
```
COS_SECRET_ID=...
COS_SECRET_KEY=...
COS_REGION=ap-guangzhou
COS_BUCKET=your-bucket-name
```

**Alternatives considered**:
- **MongoDB**：文档模型灵活，但关系查询（知识库版本关联、偏差追溯）不如 PostgreSQL；不选
- **SQLite**：轻量但不支持并发写入，不适合异步任务队列场景；不选
- **直接流式读取 COS**（不下载到本地）：OpenCV 和 MediaPipe 不原生支持流式视频源，
  需要完整文件或内存缓冲；下载到临时文件是最简单可靠的方案；不选流式方案

---

## 5. API 框架

### Decision: FastAPI（Python）

**Rationale**:
- Python 生态与 MediaPipe、OpenCV、Celery 天然集成，无跨语言调用开销
- 自动生成 OpenAPI 文档，满足 FR-007 结构化数据接口要求，支持契约测试
- 原生异步支持（async/await），适合提交视频后立即返回 task_id 的异步接口模式
- Pydantic 数据验证与序列化，保证输入输出结构严格可测

**Python 版本**: 3.11（LTS，MediaPipe 官方支持，类型提示完整）

---

## 6. 动作分割与识别方案

### Decision: 基于关键点时序特征的规则分类器（v1）+ 预留 ML 分类器接口

**Rationale**:
- v1 仅支持正手拉球和反手拨球 2 类动作（SC 范围约束）
- 两类动作的区分特征明确（主要挥拍方向、手腕位置、肘部角度变化方向），规则分类器
  可在有标注数据前快速实现高精度，满足 SC-001/SC-002 精度要求
- 接口设计预留 `ActionClassifier` 抽象，v2 可无缝替换为 CNN/LSTM 分类器

**动作片段分割方法**: 基于球拍加速度峰值（腕关节关键点速度曲线）检测击球点，
以击球点为中心取前后各 0.5 秒作为动作片段

---

## 7. 技术偏差计算方案

### Decision: 关键点角度/位移偏差计算 + 百分位数归一化

**Rationale**:
- 专家标准以关键关节角度范围（min/max/ideal）存储（ExpertTechPoint 实体）
- 偏差值 = 运动员实测值 - 专家理想值，偏差方向 = 正/负
- 影响程度评分 = 偏差值归一化到专家标准范围的百分位（偏差越大，得分越高），
  作为建议优先级排序依据（SC-003 影响程度评分有量化依据）

---

## 8. 未解决项（推迟至实现阶段）

- **视频加密存储**具体实现（AES-256 at rest，TLS 1.3 in transit）在实现任务中确定
- **知识库专家审核工作流**接口（人工确认 API）设计在契约阶段定义
- **监控与告警**方案（Prometheus + Grafana 或等效）在完善阶段添加
