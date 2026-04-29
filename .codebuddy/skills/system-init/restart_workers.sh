#!/usr/bin/env bash
# ============================================================================
# system-init 配套脚本：统一拉起全部 Celery Worker（Feature-013/016 架构）
#
# 用途：system-init 清库后，若步骤 2 停掉了 Worker，用此脚本一键恢复
# 调用：bash .codebuddy/skills/system-init/restart_workers.sh
# 幂等：先 pkill 再拉起；多次执行结果一致
# ============================================================================
set -euo pipefail

PY=/opt/conda/envs/coaching/bin
LOG_DIR=/tmp

echo '[system-init] 停止已有 Celery Worker...'
pkill -f 'celery -A src.workers.celery_app worker' 2>/dev/null || true
sleep 2

# 一队列一 Worker 物理隔离（参见 user_rule 项目规则7）
declare -a WORKERS=(
    "classification  1  classification_worker"
    "kb_extraction   2  kb_extraction_worker"
    "diagnosis       2  diagnosis_worker"
    "default         1  default_worker"
    "preprocessing   3  preprocessing_worker"
)

for w in "${WORKERS[@]}"; do
    # shellcheck disable=SC2206
    parts=($w)
    queue=${parts[0]}
    conc=${parts[1]}
    name=${parts[2]}
    log=${LOG_DIR}/celery_${name}.log
    : > "$log"
    echo "[system-init] 启动 ${name} (Q=${queue}, concurrency=${conc})"
    setsid "$PY/celery" -A src.workers.celery_app worker \
        --loglevel=info --concurrency="$conc" \
        -Q "$queue" -n "${name}@%h" >> "$log" 2>&1 < /dev/null &
    disown
done

sleep 5
echo '[system-init] Worker 启动完成，最近启动日志：'
for w in "${WORKERS[@]}"; do
    parts=($w); name=${parts[2]}
    echo "--- ${name} ---"
    grep -E 'ready|ERROR|Traceback' "${LOG_DIR}/celery_${name}.log" | tail -3 || true
done
