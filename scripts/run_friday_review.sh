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

# CLAUDE_TIMEOUT_SECONDS 是进程级看门狗，不是业务阈值，故不进 config。
# 存在的理由：claude 登录态失效时它会挂住而不是报错退出，没有看门狗会一直吊到
# OpenClaw 任务超时，周五复盘就变成静默缺席——而缺席是最不该静默的一种失败。
CLAUDE_TIMEOUT_SECONDS=900

claude -p --model opus "$PROMPT" > outbox/review.md 2> "logs/review_${STAMP}.err" &
CLAUDE_PID=$!
WAITED=0
while kill -0 "$CLAUDE_PID" 2>/dev/null; do
    if [ "$WAITED" -ge "$CLAUDE_TIMEOUT_SECONDS" ]; then
        kill -9 "$CLAUDE_PID" 2>/dev/null
        echo "CLAUDE_TIMEOUT：headless Claude Code 超过 ${CLAUDE_TIMEOUT_SECONDS}s 未返回，已强杀。" >&2
        echo "最常见原因：claude 登录态过期（claude -p 会挂住而非报错）。请在 Mac Mini 上跑 claude login 重新登录。" >&2
        exit 1
    fi
    sleep 5
    WAITED=$((WAITED + 5))
done
wait "$CLAUDE_PID"
CLAUDE_RC=$?

if [ "$CLAUDE_RC" -ne 0 ]; then
    echo "CLAUDE_FAILED（退出码 ${CLAUDE_RC}）" >&2
    tail -40 "logs/review_${STAMP}.err" >&2
    # 不留半成品：残留的空 review.md 会让下一轮排查误以为生成过
    rm -f outbox/review.md
    exit 1
fi

if [ ! -s outbox/review.md ]; then
    echo "EMPTY_REVIEW：claude 返回空内容" >&2
    exit 1
fi

# claude 认证失败时会把错误打到 stdout 并以 0 退出——不拦就会把这段错误当复盘包推出去。
if head -5 outbox/review.md | grep -qiE "Failed to authenticate|API Error|authentication_error|Invalid API key"; then
    echo "CLAUDE_AUTH_ERROR：claude 未通过认证，输出的是错误信息不是复盘包。" >&2
    head -5 outbox/review.md >&2
    echo "请在 Mac Mini 上跑 claude login 重新登录（须为订阅账号，不要配 API key）。" >&2
    rm -f outbox/review.md
    exit 1
fi

cp outbox/review.md "logs/review_${STAMP}.md"
echo "PUSH: $(pwd)/outbox/review.md"
