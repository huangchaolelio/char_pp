# 研究报告: 全量任务查询接口

**功能**: 012-task-query-all
**日期**: 2026-04-23

## 决策 1: 列表接口端点路径

- **Decision**: 新增 `GET /api/v1/tasks`（不带 `{task_id}`），与现有端点组成完整的 CRUD 路由集合。
- **Rationale**: 现有路由 `GET /tasks/{task_id}` 是单任务查询，`GET /tasks/cos-videos` 是 COS 特化接口，缺少通用的任务列表端点。新增 `GET /tasks` 符合 REST 惯例，零冲突。
- **Alternatives considered**: 在现有端点加 query param 复用路径（会与 `{task_id}` 冲突，被排除）。

## 决策 2: 扩展 TaskStatusResponse vs 新建 Schema

- **Decision**: 新增 `TaskListItemResponse`（列表轻量版）和扩展现有 `TaskStatusResponse`（增加关联统计摘要字段）。
- **Rationale**: 列表场景需要轻量字段（避免 N+1 JOIN），详情场景需要完整关联统计。两种 schema 分离符合 API 设计最佳实践。`TaskStatusResponse` 中追加 `summary` 可选字段（仅详情端点填充）。
- **Alternatives considered**: 单一 schema 全字段返回（列表性能差，被排除）；完全新建独立 schema（重复代码多，被排除）。

## 决策 3: 分页方式

- **Decision**: 使用 offset-based 分页（`page` + `page_size`），与项目现有风格对齐。
- **Rationale**: 项目不存在游标分页先例，offset 分页对运营监控场景（按时间排序翻页）更直观，且任务表不涉及实时高频写入，幻读问题可接受。
- **Alternatives considered**: 游标分页（适合无限滚动，本功能无此需求，被排除）。

## 决策 4: 关联统计摘要的查询方式

- **Decision**: 使用 SQLAlchemy `func.count()` 子查询（selectinload + subquery count），避免 ORM 懒加载。
- **Rationale**: 直接 `len(task.expert_tech_points)` 会触发全量 SELECT，COUNT 子查询效率更高。对于列表端点不返回摘要，只在详情端点执行 COUNT 查询。
- **Alternatives considered**: 冗余计数字段存入 `analysis_tasks`（需维护一致性，复杂度高，被排除）。

## 决策 5: NULL 排序处理

- **Decision**: 对 `completed_at` 排序使用 SQLAlchemy `.nullslast()`，对 `created_at` 排序无需特殊处理（`created_at` 不为 NULL）。
- **Rationale**: 澄清会话已确认 NULLS LAST，SQLAlchemy 2.0 支持 `.nullslast()` / `.nullsfirst()` 方法链。
- **Alternatives considered**: 数据库层 COALESCE（可行，但 ORM 层处理更统一）。

## 决策 6: video_storage_uri 返回

- **Decision**: 列表和详情接口均返回 `video_storage_uri` 原始值。
- **Rationale**: 澄清会话已确认（选项 A），内网接口，安全边界由网络层保障。
- **Alternatives considered**: 脱敏或不返回（用户已排除）。

## 无需研究项

- 测试框架：已有 pytest + httpx（项目现有测试结构）
- 数据库驱动：asyncpg（已锁定）
- 路由挂载：与现有 `/api/v1/tasks` 路由器相同文件扩展
