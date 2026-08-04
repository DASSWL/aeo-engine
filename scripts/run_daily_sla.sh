#!/bin/bash
# AEO Engine · daily_sla 的执行体。
# 出处：Build Spec · Phase 1 §四「daily_sla：进入 ~/aeo-engine 跑 sla_check.py，
#       有 outbox 产出才交 agent 推送」。
#
# 约定的对外接口（OpenClaw 任务只看这两样）：
#   stdout 以 PUSH: <文件路径> 开头  → 该文件内容需要推送
#   stdout 为 NO_ALERT              → 无超时项，不要推送，不要打扰
#   退出码非 0                      → 执行失败，把 stderr 摘要推出去（失败必须被看见）
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

TODAY=$(TZ=America/Los_Angeles date +%Y-%m-%d)
OUT="outbox/sla_${TODAY}.md"
rm -f "$OUT"

mkdir -p logs
if ! python3 scripts/sla_check.py > "logs/sla_${TODAY}.json" 2> "logs/sla_${TODAY}.err"; then
    echo "SLA_CHECK_FAILED" >&2
    tail -40 "logs/sla_${TODAY}.err" >&2
    exit 1
fi

if [ -s "$OUT" ]; then
    echo "PUSH: $(pwd)/${OUT}"
else
    echo "NO_ALERT"
fi
