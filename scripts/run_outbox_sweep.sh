#!/bin/bash
# AEO Engine · outbox 兜底转发的执行体。
# 存在理由见 scripts/outbox_sweep.py 的 docstring：Claude scheduled task 只能写 outbox，
# 够不着 Telegram；而四个 OpenClaw 任务各自只读自己脚本的 stdout，没人扫 outbox 目录。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN
STAMP=$(TZ=America/Los_Angeles date +%Y-%m-%d)
mkdir -p logs outbox data
if ! python3 scripts/outbox_sweep.py --commit > "logs/outbox_sweep_stdout_${STAMP}.txt" 2> "logs/outbox_sweep_${STAMP}.err"; then
    echo "OUTBOX_SWEEP_FAILED" >&2
    tail -40 "logs/outbox_sweep_${STAMP}.err" >&2
    exit 1
fi
grep '^PUSH: ' "logs/outbox_sweep_stdout_${STAMP}.txt" || {
    echo "NO_UNCLAIMED：outbox 里没有待转发的无归属报告。" >&2
    exit 0
}
