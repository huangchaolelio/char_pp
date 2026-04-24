---
alwaysApply: true
---

# Python 代码风格

- 所有函数参数和返回值必须有类型注解，使用 Python 3.10+ union 写法：`X | None`，禁止 `Optional[X]`
- 异步优先：所有数据库操作使用 `async/await` + `async_session_factory`，禁止同步 session
- Pydantic v2 语法：Schema 模型使用 `model_config = ConfigDict(...)`，禁止 v1 的 `class Config`
- 枚举：技术类别使用 `TECH_CATEGORIES` 枚举（定义在 `src/services/tech_classifier.py`），禁止字符串字面量散落代码
- 日志：使用标准 `logging` 模块，禁止 `print`
- 错误处理：服务层抛 `ValueError` 或自定义异常，路由层统一转 `HTTPException`

# 分层架构规则

- 业务逻辑只放 `src/services/`
- 路由层（`src/api/routers/`）只做参数校验和响应组装，不含任何业务逻辑
- Celery 任务（`src/workers/`）调用 service 层，不直接操作数据库以外的逻辑

# Python 运行环境

- 始终使用项目虚拟环境：`/opt/conda/envs/coaching/bin/python3.11`
- pytest：`/opt/conda/envs/coaching/bin/python3.11 -m pytest`
- pip：`/opt/conda/envs/coaching/bin/pip install`
- 禁止使用系统默认 python（3.9），不为适配 3.9 修改源代码类型注解
