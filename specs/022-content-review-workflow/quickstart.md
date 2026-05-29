# Feature-022 Quickstart: 内容审核工作流端到端验证

**关联**: [plan.md](./plan.md) | [spec.md](./spec.md) | [data-model.md](./data-model.md) | [contracts/](./contracts/)
**目标**: 通过 5 步演练验证「内容准备」阶段四阶段化 + 审核门 + KB 抽取门控的完整链路是否符合 spec.md 的 4 个用户故事。

---

## 0. 前置准备

- 项目虚拟环境：`/opt/conda/envs/coaching/bin/python3.11`
- API 监听：`http://127.0.0.1:8080`
- 鉴权：所有审核接口走 `X-Admin-Token: $ADMIN_RESET_TOKEN`（来自 `.env`）
- 演练用素材：在 COS `COS_VIDEO_ALL_COCAH` 路径下放置一段 < 30s 的测试视频

```bash
# 应用迁移
alembic upgrade head        # 上线 0021_content_review_workflow

# 重启 API + Worker（参照项目规则）
pkill -f "uvicorn src.api.main"
setsid /opt/conda/envs/coaching/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8080 \
  >> /tmp/uvicorn.log 2>&1 &

# 验证迁移
psql -d coaching -c "SELECT unnest(enum_range(NULL::business_phase_enum));"
# 期望输出: CONTENT_PREP / TRAINING / STANDARDIZATION / INFERENCE
```

环境变量：

```bash
export ADMIN_TOKEN="$(grep ADMIN_RESET_TOKEN .env | cut -d= -f2)"
export REVIEWER_ID="ops-quickstart"
export API="http://127.0.0.1:8080/api/v1"
```

---

## 1. 步骤 1：触发 COS 扫描，落入 `CONTENT_PREP` 阶段（验证 US1）

```bash
curl -s -X POST "$API/classifications/scan" \
  -H "Content-Type: application/json" \
  -d '{"force_full_rescan": false}' | jq .
```

**预期**：
- 返回 `success=true`，`data.task_id` 为 UUID
- 该任务在 `analysis_tasks.business_phase` 列值为 `'CONTENT_PREP'`（验证 US1.AC1）

```bash
# 查询阶段视图
psql -d coaching -c "
  SELECT business_phase, COUNT(*) FROM analysis_tasks
  WHERE created_at > now() - interval '1 hour'
  GROUP BY business_phase ORDER BY business_phase;
"
```

**预期**：能看到 `CONTENT_PREP` 行（无 `TRAINING` 行混入扫描任务）—— **US1.AC1/AC3 通过**。

---

## 2. 步骤 2：完成清洗，自动落 `pending_review`（验证 US2.AC1）

任选一条 `tech_category` 已分类、`preprocessed=true` 的视频，提交清洗：

```bash
COS_KEY="charhuang/tt_video/乒乓球合集【较新】/<your-test-folder>/001.mp4"

curl -s -X POST "$API/tasks" \
  -H "Content-Type: application/json" \
  -d "{
        \"task_type\": \"video_curation\",
        \"cos_object_key\": \"$COS_KEY\"
      }" | jq .
```

等待清洗完成（≈ 30s），然后查询：

```bash
psql -d coaching -c "
  SELECT id, review_state, review_version, pending_since, last_decision_id
  FROM coach_video_classifications
  WHERE cos_object_key = '$COS_KEY';
"
```

**预期**：
- `review_state = 'pending_review'`
- `pending_since` 为刚才清洗成功时刻
- `last_decision_id IS NULL`（尚未决策）

记录该行的 `id` 为 `$CVCLF_ID`（后面要用）：

```bash
export CVCLF_ID="<上面查到的 id>"
```

---

## 3. 步骤 3：直接提交 KB 抽取应被审核门拒绝（验证 US2.AC3 + FR-009）

```bash
curl -s -X POST "$API/tasks/kb-extraction" \
  -H "Content-Type: application/json" \
  -d "{
        \"cos_object_key\": \"$COS_KEY\",
        \"enable_audio_analysis\": true
      }" | jq .
```

**预期**：HTTP 409，错误信封：

```json
{
  "success": false,
  "error": {
    "code": "CONTENT_NOT_REVIEWED",
    "message": "视频尚未通过内容审核，请先在审核工作台提交决策",
    "details": {
      "cos_object_key": "...",
      "cvclf_id": "...",
      "current_review_state": "pending_review"
    }
  }
}
```

**US2.AC3 + FR-009 通过**：审核门成功拦截了未通过审核的 KB 抽取请求。

---

## 4. 步骤 4：审核工作台浏览 + 提交"通过"决策（验证 US3 + US2.AC2）

### 4.1 列出待审核条目（US3.AC1）

```bash
curl -s -X GET "$API/content-reviews?state=pending_review&page=1&page_size=20" \
  -H "X-Admin-Token: $ADMIN_TOKEN" | jq .
```

**预期**：`success=true`，`data` 数组中能看到 `$CVCLF_ID` 行，`meta` 含 `page/page_size/total`。

### 4.2 查看详情（US3.AC2）

```bash
curl -s -X GET "$API/content-reviews/$CVCLF_ID" \
  -H "X-Admin-Token: $ADMIN_TOKEN" | jq .
```

**预期**：`data` 含 `curation_summary`（清洗摘要：片段总数、保留率、≤5 个样例片段）。

### 4.3 提交"通过"决策（US2.AC2）

```bash
curl -s -X POST "$API/content-reviews/$CVCLF_ID/decisions" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "X-Reviewer-Id: $REVIEWER_ID" \
  -d "{
        \"decision\": \"approved\",
        \"reviewer_id\": \"$REVIEWER_ID\",
        \"expected_review_version\": 0
      }" | jq .
```

**预期**：HTTP 200，`data` 是新写入的 `ReviewDecision`，含 `id` / `decided_at`。

验证状态切换：

```bash
psql -d coaching -c "
  SELECT review_state, review_version, last_decision_id
  FROM coach_video_classifications WHERE id = '$CVCLF_ID';
"
```

**预期**：
- `review_state = 'approved'`
- `review_version = 1`（自增）
- `last_decision_id` 指向新决策行

---

## 5. 步骤 5：再次提交 KB 抽取，本次应入队成功（验证 US2.AC2 闭环）

```bash
curl -s -X POST "$API/tasks/kb-extraction" \
  -H "Content-Type: application/json" \
  -d "{
        \"cos_object_key\": \"$COS_KEY\",
        \"enable_audio_analysis\": true
      }" | jq .
```

**预期**：HTTP 200，`data.task_id` 返回，`data.business_phase = 'TRAINING'`（不再是 `CONTENT_PREP`）—— **US1.AC2 通过**。

---

## 6. 附加演练（可选）

### 6.1 重洗后审核失效（US2.AC4 + FR-011）

```bash
# 再次清洗同一条视频（清洗版本递进）
curl -s -X POST "$API/tasks" -H "Content-Type: application/json" \
  -d "{\"task_type\": \"video_curation\", \"cos_object_key\": \"$COS_KEY\"}"
# 等清洗完成后查询
psql -d coaching -c "SELECT review_state FROM coach_video_classifications WHERE id = '$CVCLF_ID';"
# 预期: review_state = 'pending_review'（从 approved 经 stale 中转后 stale_handler 已重置）

# 再次提交 KB 抽取
curl -s -X POST "$API/tasks/kb-extraction" \
  -H "Content-Type: application/json" \
  -d "{\"cos_object_key\": \"$COS_KEY\"}"
# 预期: 409 + CONTENT_REVIEW_STALE 或 CONTENT_NOT_REVIEWED（取决于 stale_handler 是直接重置为 pending 还是先经 stale 中转）
```

### 6.2 拒绝决策 + 列表过滤（US3 + FR-010a）

```bash
# 拒绝
curl -s -X POST "$API/content-reviews/$CVCLF_ID/decisions" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "X-Reviewer-Id: $REVIEWER_ID" \
  -d "{
        \"decision\": \"rejected\",
        \"reason_code\": \"quality_low\",
        \"note\": \"前 30 秒抖动严重\",
        \"reviewer_id\": \"$REVIEWER_ID\",
        \"expected_review_version\": <当前版本>
      }"

# 默认列表（不传 state）应不包含此条
curl -s -X GET "$API/content-reviews" -H "X-Admin-Token: $ADMIN_TOKEN" | jq '.data[].id'
# 显式查询 rejected
curl -s -X GET "$API/content-reviews?state=rejected" -H "X-Admin-Token: $ADMIN_TOKEN" | jq '.data[].id'
# 预期: 仅在显式查询时才看到该条；DB 行未被删除
```

### 6.3 审核门绕过开关（US4 + FR-014 + SC-007）

```bash
# 切换为绕过
START=$(date +%s)
curl -s -X PATCH "$API/admin/review-gate" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -d "{
        \"enabled\": false,
        \"operator_id\": \"sre-quickstart\",
        \"reason\": \"应急演练 - 审核积压\"
      }"

# 立即提交 KB 抽取（即使条目处于 pending_review 也应放行）
sleep 30      # 验证 30 秒内热生效（SC-007）
curl -s -X POST "$API/tasks/kb-extraction" \
  -H "Content-Type: application/json" \
  -d "{\"cos_object_key\": \"$COS_KEY\"}"
# 预期: 200，但响应 header 含 X-Review-Gate-Bypass: true

# 切回严格
curl -s -X PATCH "$API/admin/review-gate" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -d "{\"enabled\": true, \"operator_id\": \"sre-quickstart\", \"reason\": \"演练结束\"}"

END=$(date +%s)
echo "切换全流程耗时: $((END - START)) 秒（应 ≤ 30）"
```

### 6.4 审核统计（US3.AC4）

```bash
curl -s -X GET "$API/content-reviews/stats?from=2026-05-28T00:00:00&to=2026-05-29T00:00:00&group_by=reviewer" \
  -H "X-Admin-Token: $ADMIN_TOKEN" | jq .
```

**预期**：返回总量、通过率、平均时延、人均吞吐。

---

## 7. 验收对照表

| 用户故事 | 验收场景 | Quickstart 步骤 |
|---------|---------|----------------|
| US1.AC1 | 新素材自动落 CONTENT_PREP | 步骤 1 |
| US1.AC2 | 审核通过后 KB 抽取归 TRAINING | 步骤 5 |
| US1.AC3 | 阶段独立统计 | 步骤 1 末尾 |
| US1.AC4 | STD/INF 阶段不变 | 不在本演练（属于回归测试）|
| US2.AC1 | 清洗完成自动 pending_review | 步骤 2 |
| US2.AC2 | 审核通过 → KB 可消费 | 步骤 4.3 + 步骤 5 |
| US2.AC3 | 未审核 → KB 拒绝 | 步骤 3 |
| US2.AC4 | 重洗 → 审核失效 | 6.1 |
| US3.AC1 | 工作台列表 | 4.1 |
| US3.AC2 | 详情含清洗摘要 | 4.2 |
| US3.AC3 | 决策记录持久 | 4.3 末尾 |
| US3.AC4 | 统计接口 | 6.4 |
| US4.AC1 | 阶段维度可观测 | （待 T025-T027 落地后验证）|
| US4.AC2 | 绕过开关 | 6.3 |
| US4.AC3 | 切回严格不留豁免 | 6.3 |

---

## 8. 故障排查

| 症状 | 排查方向 |
|------|---------|
| 步骤 3 未返回 409 | 审核门未接入 `submit_kb_extraction` 路由；检查 tasks.py 中 `evaluate_review_gate` 调用位置 |
| 步骤 5 仍返回 `business_phase=CONTENT_PREP` | 任务派发时未根据 task_type 计算 phase；检查 `task_submission_service` 的 phase 推导逻辑 |
| 6.3 切换后超过 30s 仍未生效 | hot-config 刷新周期被卡；检查 `task_channel_configs` 缓存失效逻辑（与 Feature-018 对齐）|
| 6.1 重洗后未自动回到 pending_review | `stale_handler` 未挂上清洗成功回调；检查 `curation_service.py` 调用链 |
