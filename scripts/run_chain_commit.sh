#!/bin/bash
# AEO Engine · Query 库进料链的通用自动写入执行体（2026-08-05 Shawn 拍板去人工化后新增）。
#
# 用法：run_chain_commit.sh <脚本名不带.py> [额外参数...]
#   run_chain_commit.sh query_candidates --pool     # 候选池 → Query 库
#   run_chain_commit.sh gsc_queries                 # GSC API → Query 库
#   run_chain_commit.sh scan_queries                # 水箱 A1 扫描原话 → Query 库
#   run_chain_commit.sh apollo_poll                 # Apollo 名单 → 水箱
#
# 行为契约（与 run_daily_sla.sh 同一形态）：
#   写入 0 条且无错误 → stdout NO_ALERT，退出码 0，不打扰任何人
#   写入 >0 条        → stdout PUSH: <摘要文件>，agent 原样推群
#   任何失败（含缺凭据退出码 2）→ PUSH 失败摘要，失败必须被看见
#
# --commit 由本执行体统一追加：脚本本身保持默认 dry-run 的纪律，
# 「自动写入」这个决定集中在这一个文件里，要撤销只改这里。
#
# unset 两行照抄 run_a1_health.sh：本脚本自身不调 claude，但凡经 OpenClaw 派生的
# 执行体都保持同一形态，免得哪天有人往里加一行 claude 调用就开始按 token 计费。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN

SCRIPT="${1:?用法: run_chain_commit.sh <脚本名> [参数...]}"
shift

STAMP=$(TZ=America/Los_Angeles date +%Y-%m-%d)
OUT="logs/${SCRIPT}_cron_${STAMP}.json"
ERR="logs/${SCRIPT}_cron_${STAMP}.err"
SUMMARY="outbox/${SCRIPT}_cron_${STAMP}.md"
mkdir -p logs outbox data

python3 "scripts/${SCRIPT}.py" "$@" --commit > "$OUT" 2> "$ERR"
RC=$?

if [ "$RC" -ne 0 ]; then
    {
        echo "⚠️ AEO ${SCRIPT} 自动写入失败（退出码 ${RC}）"
        echo
        python3 - "$OUT" 2>/dev/null <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print("status:", d.get("status"))
    if d.get("missing"):
        print("missing:", d["missing"])
except Exception:
    pass
PY
        echo '```'
        tail -15 "$ERR" 2>/dev/null || echo "(无 stderr)"
        echo '```'
    } > "$SUMMARY"
    echo "PUSH: $(pwd)/${SUMMARY}"
    exit "$RC"
fi

WRITTEN=$(python3 - "$OUT" 2>/dev/null <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(len(d.get("written") or []))
PY
) || WRITTEN="?"

if [ "$WRITTEN" = "0" ]; then
    echo "NO_ALERT"
    exit 0
fi

{
    echo "AEO ${SCRIPT} · ${STAMP} 自动写入 ${WRITTEN} 条"
    echo
    python3 - "$OUT" 2>/dev/null <<'PY' || true
import json, sys
d = json.load(open(sys.argv[1]))
for w in (d.get("written") or [])[:20]:
    label = w.get("query 文本") or w.get("人名") or w.get("公司") or w.get("action")
    print("- {}".format(label))
n = len(d.get("written") or [])
if n > 20:
    print("- …共 {} 条，其余见 logs/".format(n))
PY
    echo
    echo "（自动写入 · 定期 review 时按「数据来源」列核对成色）"
} > "$SUMMARY"
echo "PUSH: $(pwd)/${SUMMARY}"
exit 0
