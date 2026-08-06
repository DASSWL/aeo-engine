#!/bin/bash
# AEO Engine · Query 库进料链的每周执行体。
#
# 一个执行体跑两个脚本，因为它们是同一件事的两半：
#   query_intake_health.py  —— 四条进料链现在断在哪
#   buyer_quote_queries.py  —— 唯一以真实对话为源的那条链本周有没有燃料
# 分成两个 cron 会让「链断了」和「链有料了」在群里隔着几分钟分别出现，
# 而真人要看的是合起来的那一眼。
#
# query_intake_health 只读；buyer_quote_queries 自 2026-08-05 Shawn 拍板
# 「Query 库写入去人工化」后改为带 --commit 写库（此前本执行体刻意不给该参数）。
# 两者都不调任何计费 API，零对外发送。
#
# 排在 a1_health（周五 14:30）之前，让周五复盘拿到「query 从哪来」的现状——
# J1 第 5 类 AEO 内容的输入就是 Query 库，库不长那条产线就没有新料。
#
# unset 两行照抄 run_a1_health.sh：本脚本自身不调 claude，但凡经 OpenClaw 派生的
# 执行体都保持同一形态，免得哪天有人往里加一行 claude 调用就开始按 token 计费。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN
STAMP=$(TZ=America/Los_Angeles date +%Y-%m-%d)
mkdir -p logs outbox data

FAILED=0

if ! python3 scripts/query_intake_health.py --commit \
        > "logs/query_intake_health_stdout_${STAMP}.txt" \
        2> "logs/query_intake_health_${STAMP}.err"; then
    echo "QUERY_INTAKE_HEALTH_FAILED" >&2
    tail -40 "logs/query_intake_health_${STAMP}.err" >&2
    FAILED=1
else
    grep '^PUSH: ' "logs/query_intake_health_stdout_${STAMP}.txt" \
        || { echo "NO_INTAKE_REPORT" >&2; FAILED=1; }
fi

# --review 写 outbox 审核清单；--commit 同时写 Query 库（2026-08-05 起自动写入）。
if ! python3 scripts/buyer_quote_queries.py --review --commit \
        > "logs/buyer_quote_queries_stdout_${STAMP}.txt" \
        2> "logs/buyer_quote_queries_${STAMP}.err"; then
    echo "BUYER_QUOTE_FAILED" >&2
    tail -40 "logs/buyer_quote_queries_${STAMP}.err" >&2
    FAILED=1
else
    grep '^PUSH: ' "logs/buyer_quote_queries_stdout_${STAMP}.txt" \
        || { echo "NO_BUYER_QUOTE_REPORT" >&2; FAILED=1; }
fi

exit "$FAILED"
