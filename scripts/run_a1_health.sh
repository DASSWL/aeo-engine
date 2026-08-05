#!/bin/bash
# AEO Engine · A1 感知层观测的执行体。
# 排在 friday_review（15:00）之前跑，让砍留 segment 的讨论拿到分来源的填补速度，
# 而不是 metrics.py 那个把 Apollo 与扫描混在一起的 weekly_inbox。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN
STAMP=$(TZ=America/Los_Angeles date +%Y-%m-%d)
mkdir -p logs outbox data
if ! python3 scripts/a1_health.py --commit > "logs/a1_health_stdout_${STAMP}.txt" 2> "logs/a1_health_${STAMP}.err"; then
    echo "A1_HEALTH_FAILED" >&2
    tail -40 "logs/a1_health_${STAMP}.err" >&2
    exit 1
fi
grep '^PUSH: ' "logs/a1_health_stdout_${STAMP}.txt" || { echo "NO_REPORT" >&2; exit 1; }
