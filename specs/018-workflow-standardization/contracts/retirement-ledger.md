# Feature-018 下线接口台账

本 Feature **未下线任何接口**。

本 Feature 为「观测与治理」层加固：
- 新增接口 2 个：`GET /api/v1/business-workflow/overview`、`GET /api/v1/admin/levers`
- 扩展既有接口查询参数：`GET /api/v1/tasks` / `GET /api/v1/extraction-jobs` / `GET /api/v1/knowledge-base/versions` 新增 `?business_phase=` / `?business_step=` 可选参数
- 无下线、无废弃、无路由删除

保留本文件是为满足章程 v1.4.0 原则 IX「新增/变更接口必须在 `contracts/` 下提供契约」的结构完整性要求；未来如有下线动作，按 Feature-017 `retirement-ledger.md` 格式追加条目（只可追加，不可删除）。
