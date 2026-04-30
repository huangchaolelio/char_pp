# Feature-018 — 本地双层 CI 守卫（Q6 决议：不引入托管 CI 平台配置）
# 目标说明见 specs/018-workflow-standardization/spec.md FR-011
#
# 使用：
#   make drift-changed     — pre-push 阶段推荐（仅扫 diff 涉及清单）
#   make drift-full        — master 合并前最终闸门（全量扫描）
#   make spec-compliance   — 单独跑 Spec 合规扫描
#
# 环境变量覆盖：
#   PYBIN=/path/to/python make drift-full
#
# 未来接入任意托管 CI（GitHub Actions / GitLab / Jenkins）时，仅需在对应配置中
# 调用下列 Makefile 目标即可。

PYBIN ?= /opt/conda/envs/coaching/bin/python3.11

.PHONY: drift-changed drift-full spec-compliance

drift-changed:
	@$(PYBIN) -m scripts.audit.workflow_drift --changed-only
	@$(PYBIN) -m scripts.audit.spec_compliance --changed-only

drift-full:
	@$(PYBIN) -m scripts.audit.workflow_drift --full
	@$(PYBIN) -m scripts.audit.spec_compliance --full

spec-compliance:
	@$(PYBIN) -m scripts.audit.spec_compliance --full
