#!/bin/bash
# AEO Engine · J4 draft_runner 的执行体。
#
# 出处：Build Spec · Phase 3「J4 Outreach」。
# 对外接口同 run_daily_sla.sh / run_friday_review.sh：
#   PUSH: <路径>  每条草稿一行；退出码非 0 即失败。
#
# 三段：plan（纯 Python）→ claude -p（加载真 skill 写英文）→ assemble（纯 Python）。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# ---------------------------------------------------------------------------
# 强制走订阅登录，剥掉按 token 计费的凭据。
#
# 与 run_friday_review.sh 第 26-27 行同一处理，理由完整版见那里：OpenClaw 的
# openclaw.json 配了 env.ANTHROPIC_API_KEY，会注入派生的每一个进程；claude 见到它就
# 优先用它而不是 claude.ai 订阅登录，于是每天跑一次草稿就每天按 token 计费一次。
#
# Phase 1 §八① 的红字提醒「同类风险未清除：凡经 OpenClaw 派生、又会调用 claude CLI 的
# 其他自动化，都存在同样的静默转 API 计费问题」——J4 就是那个「其他自动化」，
# 所以这一段必须复用，不是可选项。
# ---------------------------------------------------------------------------
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN
# ---------------------------------------------------------------------------

STAMP=$(TZ=America/Los_Angeles date +%Y-%m-%d)
mkdir -p logs outbox data

PROMPT_FILE="logs/j4_prompt_${STAMP}.md"
CLAUDE_OUT="logs/j4_claude_${STAMP}.txt"
CLAUDE_ERR="logs/j4_claude_${STAMP}.err"

# 1) skill 暂存。缺必需 skill 不是致命错——受影响环节会 refuse，其余照跑。
#    退出码 3 = 有缺失，如实记进日志，不中断。
python3 scripts/skill_check.py --stage > "logs/j4_skills_${STAMP}.json" 2>&1
SKILL_RC=$?
if [ "$SKILL_RC" -eq 3 ]; then
    echo "SKILL_MISSING（详见 logs/j4_skills_${STAMP}.json）受影响环节将 refuse，不顶替。" >&2
elif [ "$SKILL_RC" -ne 0 ]; then
    echo "SKILL_CHECK_FAILED（退出码 ${SKILL_RC}）" >&2
    cat "logs/j4_skills_${STAMP}.json" >&2
    exit 1
fi

# 2) plan：算队列、定切入角与渠道、过证据闸门，并写出 claude prompt
if ! python3 scripts/draft_runner.py --emit-prompt "$PROMPT_FILE" \
        > "logs/j4_plan_stdout_${STAMP}.json" 2> "logs/j4_plan_${STAMP}.err"; then
    echo "PLAN_FAILED" >&2
    tail -40 "logs/j4_plan_${STAMP}.err" >&2
    exit 1
fi

PLANNED=$(grep -o 'DRAFTS_PLANNED: [0-9]*' "logs/j4_plan_${STAMP}.err" | tail -1 | awk '{print $2}')
PLANNED=${PLANNED:-0}
if [ "$PLANNED" -eq 0 ]; then
    # 队列为空是正常状态（没到期的行）。与 daily_sla 同口径：无内容即不打扰。
    echo "NO_DRAFTS：待触达队列为空，不写 outbox，不推送。" >&2
    exit 0
fi

# 3) headless Claude Code 写草稿正文。真 skill 已暂存在 .claude/skills/。
#
# --add-dir：ai-writing-guideline 是指针 skill，规则在仓库外的 ai_writings.md 里。
# 不授权它就只能用 fallback 子集（2026-08-04 实测过一次，见 outreach.yaml 的注释）。
# 目录清单从 config 读，脚本里不写死路径。
CLAUDE_TIMEOUT_SECONDS=900
ADD_DIR_ARGS=()
while IFS= read -r d; do
    [ -n "$d" ] && ADD_DIR_ARGS+=(--add-dir "$d")
done < <(python3 -c "
import sys, yaml
cfg = yaml.safe_load(open('config/outreach.yaml'))
for d in (cfg['skills'].get('extra_read_dirs') or []):
    print(d)
")

# prompt 走 stdin 而不是位置参数：--add-dir 是可变长选项，后面跟位置参数会被它吞掉，
# claude 报 "Input must be provided either through stdin or as a prompt argument"。
# 2026-08-04 实测踩到，改 stdin 后正常。
claude -p --model opus "${ADD_DIR_ARGS[@]}" < "$PROMPT_FILE" > "$CLAUDE_OUT" 2> "$CLAUDE_ERR" &
CLAUDE_PID=$!
WAITED=0
while kill -0 "$CLAUDE_PID" 2>/dev/null; do
    if [ "$WAITED" -ge "$CLAUDE_TIMEOUT_SECONDS" ]; then
        kill -9 "$CLAUDE_PID" 2>/dev/null
        echo "CLAUDE_TIMEOUT：headless Claude Code 超过 ${CLAUDE_TIMEOUT_SECONDS}s 未返回，已强杀。" >&2
        echo "最常见原因：claude 登录态过期（claude -p 会挂住而非报错）。请在 Mac Mini 上跑 claude login。" >&2
        rm -f "$CLAUDE_OUT"
        exit 1
    fi
    sleep 5
    WAITED=$((WAITED + 5))
done
wait "$CLAUDE_PID"
CLAUDE_RC=$?

if [ "$CLAUDE_RC" -ne 0 ]; then
    echo "CLAUDE_FAILED（退出码 ${CLAUDE_RC}）" >&2
    tail -40 "$CLAUDE_ERR" >&2
    rm -f "$CLAUDE_OUT"
    exit 1
fi
if [ ! -s "$CLAUDE_OUT" ]; then
    echo "EMPTY_DRAFTS：claude 返回空内容" >&2
    exit 1
fi
# claude 认证失败会把错误打到 stdout 并以 0 退出——不拦就会把错误当草稿推出去。
if head -5 "$CLAUDE_OUT" | grep -qiE "Failed to authenticate|API Error|authentication_error|Invalid API key"; then
    echo "CLAUDE_AUTH_ERROR：claude 未通过认证，输出的是错误信息不是草稿。" >&2
    head -5 "$CLAUDE_OUT" >&2
    rm -f "$CLAUDE_OUT"
    exit 1
fi

# 4) assemble：装进 spec 的 Telegram 模板，写 outbox
if ! python3 scripts/draft_runner.py --assemble "$CLAUDE_OUT" --commit \
        > "logs/j4_assemble_${STAMP}.json" 2>&1; then
    echo "ASSEMBLE_FAILED" >&2
    tail -40 "logs/j4_assemble_${STAMP}.json" >&2
    exit 1
fi

# 5) 每条草稿一行 PUSH。sales agent 逐条发到群里——一条一消息是回执协议的前提，
#    混在一条里就没法按行 ID 一一对应确认。
for f in outbox/j4_draft_${STAMP}_*.md; do
    [ -e "$f" ] || continue
    echo "PUSH: $(pwd)/$f"
done
