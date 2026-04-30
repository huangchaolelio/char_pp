#!/usr/bin/env bash
# Feature-018 — 幂等软链安装器：把 scripts/git-hooks/pre-push 挂到 .git/hooks/pre-push.
#
# 幂等：重复执行不会报错，软链永远指向仓库内脚本。

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

HOOK_SRC="scripts/git-hooks/pre-push"
HOOK_DST=".git/hooks/pre-push"

if [ ! -f "$HOOK_SRC" ]; then
    echo "[install-git-hooks] ERROR: $HOOK_SRC not found"
    exit 1
fi

chmod +x "$HOOK_SRC"

# 软链到 .git/hooks（相对路径，跟随仓库移动）
ln -sfn "../../$HOOK_SRC" "$HOOK_DST"

echo "[install-git-hooks] symlinked $HOOK_DST -> $HOOK_SRC"
echo "[install-git-hooks] Feature-018 pre-push drift scan is now active."
