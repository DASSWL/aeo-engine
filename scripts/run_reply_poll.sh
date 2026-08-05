#!/bin/bash
# AEO Engine · J4 回流轮询的执行体。
# 出处：Build Spec · Phase 3 §J4 补充「回流轮询每日一次，随 daily_sla 之后运行」。
# 对外接口：PUSH: <路径> 每条草稿一行；退出码非 0 即失败。
#
# 本脚本零 LLM（回复草稿是固定骨架，见 reply_poll.py 的 draft_message 注释），
# 所以不需要 claude；但 unset 仍然保留——本脚本由 OpenClaw 派生，
# 将来若加了 claude 调用，忘记 unset 就会静默转按量计费（Phase 1 §八①）。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN

STAMP=$(TZ=America/Los_Angeles date +%Y-%m-%d)
mkdir -p logs outbox data

if ! python3 scripts/reply_poll.py --commit > "logs/reply_poll_stdout_${STAMP}.txt" 2> "logs/reply_poll_${STAMP}.err"; then
    echo "REPLY_POLL_FAILED" >&2
    tail -40 "logs/reply_poll_${STAMP}.err" >&2
    exit 1
fi
grep '^PUSH: ' "logs/reply_poll_stdout_${STAMP}.txt" || {
    echo "NO_REPLIES：无新回流，不推送。" >&2
    exit 0
}
