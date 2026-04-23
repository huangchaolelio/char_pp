# Feature 011 开发者快速启动指南

## 1. 前置条件

- Python 3.11+，激活虚拟环境：
  ```bash
  source .venv/bin/activate
  ```
- PostgreSQL 已运行，迁移已应用：
  ```bash
  alembic upgrade head
  ```
- Feature 010 active 标准已写入数据库（至少包含 `forehand_topspin`）
- `.env` 或 settings 已正确配置数据库连接和 LLM 参数

## 2. 启动服务

```bash
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

## 3. 测试场景

### 场景 1: 无效技术类别 → 422

```bash
curl -s -X POST http://localhost:8000/api/v1/diagnosis \
  -H "Content-Type: application/json" \
  -d '{"tech_category": "invalid_move", "video_path": "test.mp4"}' | python3 -m json.tool
```

期望响应：
```json
{"detail": [{"loc": [...], "msg": "...", "type": "value_error"}]}
```

### 场景 2: 无标准数据 → 404

```bash
curl -s -X POST http://localhost:8000/api/v1/diagnosis \
  -H "Content-Type: application/json" \
  -d '{"tech_category": "forehand_loop_underspin", "video_path": "test.mp4"}' | python3 -m json.tool
```

期望响应：
```json
{"detail": {"error": "standard_not_found", ...}}
```

### 场景 3: 正常流程（需要真实视频或 mock pipeline）

```bash
# 使用 COS 路径（需要 COS 配置）
curl -s -X POST http://localhost:8000/api/v1/diagnosis \
  -H "Content-Type: application/json" \
  -d '{"tech_category": "forehand_topspin", "video_path": "cos://your-bucket/coach_video.mp4"}' | python3 -m json.tool
```

期望响应：HTTP 200，包含 `report_id`、`overall_score` ≥ 0、`dimensions` 数组。

## 4. 运行自动化测试

```bash
# 单元测试
.venv/bin/pytest tests/unit/test_diagnosis_scorer.py tests/unit/test_diagnosis_llm_advisor.py tests/unit/test_diagnosis_service.py -v

# 合约测试（无需真实 DB）
.venv/bin/pytest tests/contract/test_diagnosis_contract.py -v

# 集成测试（需要真实 PostgreSQL）
.venv/bin/pytest tests/integration/test_diagnosis_api.py -v

# 全部 Feature 011 测试（共 92 tests）
.venv/bin/pytest tests/unit/test_diagnosis_scorer.py tests/unit/test_diagnosis_llm_advisor.py tests/unit/test_diagnosis_service.py tests/unit/test_diagnosis_service_cleanup.py tests/unit/test_migration_011.py tests/unit/test_diagnosis_model.py tests/contract/test_diagnosis_contract.py tests/integration/test_diagnosis_api.py -v
```

## 5. 验收标准检查清单

- [ ] SC-001: 教练视频评分 ≥ 80（需要真实 pipeline）
- [ ] SC-002: 端到端 ≤ 60s（curl 计时验证）
- [x] SC-004: 改进建议覆盖所有偏差维度（集成测试验证）
- [x] SC-005: 无标准时 100% 返回 404（合约+集成测试验证）
