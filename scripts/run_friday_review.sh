#!/bin/bash
# AEO Engine · friday_review 的执行体。
# 出处：Build Spec · Phase 1 §四「friday_review：跑 metrics.py → headless Claude Code
#       （claude -p 模式）读 prompts/friday_review.md 与最新 metrics JSON 生成复盘包
#       → 写 outbox/review.md → agent 推送」。
#
# 模型：Opus（spec §四 模型路由：headless Claude Code 统一用 Opus，禁止 Fable）。
# 计费：走订阅登录的 claude 命令，不配置任何按 token 计费的 API key。
#
# 对外接口同 run_daily_sla.sh：PUSH: <路径> / 退出码非 0 即失败。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

STAMP=$(TZ=America/Los_Angeles date +%Y-%m-%d)
mkdir -p logs outbox

# 1) 度量计算（纯 Python，零 LLM）
if ! python3 scripts/metrics.py > "logs/metrics_${STAMP}.json" 2> "logs/metrics_${STAMP}.err"; then
    echo "METRICS_FAILED" >&2
    tail -40 "logs/metrics_${STAMP}.err" >&2
    exit 1
fi

LATEST=$(ls -t data/metrics_*.json 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
    echo "NO_METRICS_FILE" >&2
    exit 1
fi

# 2) headless Claude Code 生成复盘包
PROMPT=$(cat <<EOF
$(cat prompts/friday_review.md)

---

# 本次输入

## metrics JSON（$LATEST）
\`\`\`json
$(cat "$LATEST")
\`\`\`

## config/gates.yaml
\`\`\`yaml
$(cat config/gates.yaml)
\`\`\`

## config/thresholds.yaml
\`\`\`yaml
$(cat config/thresholds.yaml)
\`\`\`

现在按上面的七节结构输出复盘包正文。只输出正文，不要任何前言或说明。
EOF
)

if ! claude -p --model opus "$PROMPT" > outbox/review.md 2> "logs/review_${STAMP}.err"; then
    echo "CLAUDE_FAILED" >&2
    tail -40 "logs/review_${STAMP}.err" >&2
    exit 1
fi

if [ ! -s outbox/review.md ]; then
    echo "EMPTY_REVIEW" >&2
    exit 1
fi

cp outbox/review.md "logs/review_${STAMP}.md"
echo "PUSH: $(pwd)/outbox/review.md"
