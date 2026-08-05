#!/bin/bash
# AEO Engine · daily_brief 的执行体。
#
# 依据：运行手册页「📖 AEO Engine v1」。
#
# 与 run_daily_sla.sh / run_draft_runner.sh 的关键区别：**没有 NO_ALERT 分支。**
# 那两个是「无内容不打扰」，这一个是「每天必发」——它兼任整台机器的心跳，
# 10:00 群里什么都没有就意味着 Mac Mini 或 OpenClaw 出了问题，而不是「今天没事」。
# 所以本脚本的每一条路径最后都 echo 一行 PUSH:，一条都不许静默。
#
# 对外接口：
#   stdout 恒以 PUSH: <文件路径> 开头  → 该文件内容需要原样推送
#   退出码 0 = 简报正常；非 0 = 简报是失败上报（照样要推）
#
# 没有 unset ANTHROPIC_API_KEY：本脚本零 LLM，不调 claude CLI，
# 不存在 Phase 1 §八① 那个静默转 API 计费的问题。将来若有人往这里加 claude 调用，
# 必须先照抄 run_draft_runner.sh 开头那两行。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

TODAY=$(TZ=America/Los_Angeles date +%Y-%m-%d)
OUT="outbox/brief_${TODAY}.md"
LOG="logs/brief_${TODAY}.err"

mkdir -p logs outbox
rm -f "$OUT"

python3 scripts/daily_brief.py > "logs/brief_${TODAY}.txt" 2> "$LOG"
RC=$?

# 兜底：脚本自己会在失败时也写出一条失败简报，这里只处理「连文件都没写出来」
# 的情形（磁盘满、config 目录不见了、python3 不存在）。宁可丑，也不许静默。
if [ ! -s "$OUT" ]; then
    {
        echo "⚠️ AEO Engine · 今日简报缺席 ${TODAY}"
        echo
        echo "daily_brief.py 退出码 ${RC}，且没有产出 ${OUT}。"
        echo "这条消息由执行体兜底发出——你能收到它，说明定时任务还活着。"
        echo
        echo "stderr 末尾："
        tail -20 "$LOG" 2>/dev/null || echo "(连 stderr 都没有)"
    } > "$OUT"
    echo "PUSH: $(pwd)/${OUT}"
    exit 1
fi

echo "PUSH: $(pwd)/${OUT}"
exit "$RC"
