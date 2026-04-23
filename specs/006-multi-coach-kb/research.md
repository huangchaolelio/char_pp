# Research: 多教练知识库提炼与技术校准

**功能**: 006-multi-coach-kb | **日期**: 2026-04-21

## 1. 数据库设计决策

### Coach 实体表结构

**Decision**: 新建 `coaches` 独立表，`analysis_tasks` 加 `coach_id` 可空外键（`ON DELETE SET NULL`）
**Rationale**: 
- 保持关系范式，Coach 实体独立管理（CRUD）
- `expert_tech_points` 和 `teaching_tips` 无需新增字段，通过 JOIN 获得教练信息，避免数据冗余和更新异常
- `ON DELETE SET NULL` 确保删除教练（软删除不需要，但防御性设计）不破坏历史任务数据
- 软删除用 `is_active` 布尔字段而非 `deleted_at`，简单直接

**Alternatives considered**: 
- 在 tech_points/tips 直接加 coach_id：冗余，更新一致性差，已排除
- 纯字符串存教练名：无法支持 CRUD 管理，已排除

### 迁移策略

**Decision**: 新建 Alembic 迁移 `0007_multi_coach_kb.py`，操作：
1. CREATE TABLE `coaches`（id, name UNIQUE, bio, is_active, created_at）
2. ALTER TABLE `analysis_tasks` ADD COLUMN `coach_id UUID REFERENCES coaches(id) ON DELETE SET NULL`
3. CREATE INDEX `ix_analysis_tasks_coach_id`

**Rationale**: 两步迁移合并为单文件，减少迁移文件数量；`coach_id` 可空，向后兼容现有数据

**Alternatives considered**: 分两个迁移文件：不必要的额外复杂度

---

## 2. API 设计决策

### 校准接口参数设计

**Decision**: `GET /api/v1/calibration/tech-points?action_type={}&dimension={}`，两个参数均为必填
**Rationale**: 精确返回单一维度的多教练对比，结果集小、响应快；符合 spec 澄清决策（选项 A）
**Alternatives considered**: 仅传 action_type 返回全部维度（选项 B，已被用户修正为 A）

### 教学建议校准接口

**Decision**: `GET /api/v1/calibration/teaching-tips?action_type={}&tech_phase={}` 两个参数均为必填
**Rationale**: 与 tech_points 校准接口对称；tech_phase 精确定位教学阶段，与 FR-005 一致
**Alternatives considered**: 仅传 action_type，返回过多无关建议

### 教练过滤集成

**Decision**: 在现有 `GET /api/v1/teaching-tips` 添加可选 `coach_id` 查询参数，通过 task JOIN 过滤
**Rationale**: 复用现有接口，最小化接口变更；JOIN 路径：teaching_tips.task_id → analysis_tasks.coach_id
**Alternatives considered**: 新建专用接口：不必要，增加接口数量

---

## 3. 测试策略

### 合约测试覆盖

- `tests/contract/test_coaches_api.py`：Coach CRUD（创建、查询、修改、软删除）
- `tests/contract/test_calibration_api.py`：校准接口正常和边界场景

### 集成测试覆盖

- `tests/integration/test_coach_kb_pipeline.py`：
  - 创建 Coach → 关联 ExpertVideoTask → 查询时能通过 coach_id 过滤
  - 历史无 coach_id 任务数据正常可用

### 单元测试覆盖

- `tests/unit/test_coaches_router.py`：Coach 路由 mock 测试
- `tests/unit/test_calibration_router.py`：校准路由 mock 测试

---

## 4. 向后兼容验证

**Decision**: `coach_id` 在所有新接口中为可选参数；`coach_id=NULL` 的历史数据在过滤时不传教练参数则正常返回
**Rationale**: SC-005 明确要求"现有测试全部通过，无回归"；最小化对现有接口的改动

**验证方法**: 运行现有 191 个测试（测试在 Feature-005 后全部通过），确保无回归

---

## 5. 依赖评估

**无新外部依赖**：本功能完全基于现有技术栈（FastAPI, SQLAlchemy, Alembic, PostgreSQL），不引入新包。
