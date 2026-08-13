#!/bin/bash
# AEO Engine · J1 内容产线的执行体。
#
# 出处:Build Spec · Phase 3「J1 证据生产」+ 2026-08-06 拍板(建立流程、计入台账)。
# 对外接口同 run_draft_runner.sh:PUSH: <路径> 每条通知一行;退出码非 0 即失败。
#
# 三段:plan(纯 Python)→ claude -p(加载真 skill 写英文)→ assemble(纯 Python,
# 落 outbox + 经 j1_evidence.py 登记台账「草稿」行 + 独立回读)。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# ---------------------------------------------------------------------------
# 强制走订阅登录,剥掉按 token 计费的凭据。
# 与 run_draft_runner.sh 第 23-24 行同一处理,理由完整版见 run_friday_review.sh:
# OpenClaw 会把 openclaw.json 的 env.ANTHROPIC_API_KEY 注入派生的每一个进程,
# claude 见到它就优先用它而不是订阅登录,每周跑一次就每周按 token 计费一次。
# Phase 1 §八① 红字:凡经 OpenClaw 派生、又调 claude CLI 的自动化,必须套这段。
# ---------------------------------------------------------------------------
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN
# ---------------------------------------------------------------------------

STAMP=$(TZ=America/Los_Angeles date +%Y-%m-%d)
mkdir -p logs outbox data

PROMPT_FILE="logs/j1_prompt_${STAMP}.md"
CLAUDE_OUT="logs/j1_claude_${STAMP}.txt"
CLAUDE_ERR="logs/j1_claude_${STAMP}.err"

# 1) skill 暂存。缺必需 skill 不中断——prompt 里会如实标注,不顶替。
python3 scripts/skill_check.py --stage > "logs/j1_skills_${STAMP}.json" 2>&1
SKILL_RC=$?
if [ "$SKILL_RC" -eq 3 ]; then
    echo "SKILL_MISSING(详见 logs/j1_skills_${STAMP}.json)" >&2
elif [ "$SKILL_RC" -ne 0 ]; then
    echo "SKILL_CHECK_FAILED(退出码 ${SKILL_RC})" >&2
    cat "logs/j1_skills_${STAMP}.json" >&2
    exit 1
fi

# 2) plan:选题、证据候选、写 claude prompt
if ! python3 scripts/j1_runner.py --emit-prompt "$PROMPT_FILE" \
        > "logs/j1_plan_stdout_${STAMP}.json" 2> "logs/j1_plan_${STAMP}.err"; then
    echo "PLAN_FAILED" >&2
    tail -40 "logs/j1_plan_${STAMP}.err" >&2
    exit 1
fi

# 过滤报告:plan 每轮固定产一份,无论选不选得出选题,一律推。
# Shawn 2026-08-12:「以后遇到被闸门限制住的,都给我发一个消息让我知道
# 什么东西被过滤掉了」。放在 PLANNED 判断**之前**——队列为空恰恰是最该看的那次,
# 而队列为空正是原来那条直接 exit 0 的静默路径。
FILTERED="outbox/j1_filtered_${STAMP}.md"
[ -s "$FILTERED" ] && echo "PUSH: $(pwd)/${FILTERED}"

PLANNED=$(grep -o 'DRAFTS_PLANNED: [0-9]*' "logs/j1_plan_${STAMP}.err" | tail -1 | awk '{print $2}')
PLANNED=${PLANNED:-0}
if [ "$PLANNED" -eq 0 ]; then
    # 选题队列为空:不再静默。过滤报告上面已经推了,它会说清是被哪道闸挡光的。
    echo "NO_DRAFTS:选题队列为空(过滤报告已推)。" >&2
    exit 0
fi

# 3) headless Claude Code 写正文。真 skill 已暂存在 .claude/skills/。
# --add-dir 与 stdin 的两个坑同 run_draft_runner.sh(见其 62-80 行注释)。
# 2026-08-12 由 900 提到 1800:max_per_run 从 2 提到 3,一次 claude 调用要写
# 3 篇 × 600-900 词 ≈ 2700 词,还要先读 ai-writing-guideline 指向的实时规则文件。
# 900s 在 2 篇时没量过上限,3 篇更没有——超时会强杀并丢弃全部产出(下面 rm),
# 把三篇一起赔掉。宁可等,不要赔。
CLAUDE_TIMEOUT_SECONDS=1800
ADD_DIR_ARGS=()
while IFS= read -r d; do
    [ -n "$d" ] && ADD_DIR_ARGS+=(--add-dir "$d")
done < <(python3 -c "
import yaml
cfg = yaml.safe_load(open('config/outreach.yaml'))
for d in (cfg['skills'].get('extra_read_dirs') or []):
    print(d)
")

claude -p --model opus "${ADD_DIR_ARGS[@]}" < "$PROMPT_FILE" > "$CLAUDE_OUT" 2> "$CLAUDE_ERR" &
CLAUDE_PID=$!
WAITED=0
while kill -0 "$CLAUDE_PID" 2>/dev/null; do
    if [ "$WAITED" -ge "$CLAUDE_TIMEOUT_SECONDS" ]; then
        kill -9 "$CLAUDE_PID" 2>/dev/null
        echo "CLAUDE_TIMEOUT:超过 ${CLAUDE_TIMEOUT_SECONDS}s 未返回,已强杀。" >&2
        echo "最常见原因:claude 登录态过期(claude -p 会挂住而非报错)。请在 Mac Mini 上跑 claude login。" >&2
        rm -f "$CLAUDE_OUT"
        exit 1
    fi
    sleep 5
    WAITED=$((WAITED + 5))
done
wait "$CLAUDE_PID"
CLAUDE_RC=$?

if [ "$CLAUDE_RC" -ne 0 ]; then
    echo "CLAUDE_FAILED(退出码 ${CLAUDE_RC})" >&2
    tail -40 "$CLAUDE_ERR" >&2
    rm -f "$CLAUDE_OUT"
    exit 1
fi
if [ ! -s "$CLAUDE_OUT" ]; then
    echo "EMPTY_DRAFTS:claude 返回空内容" >&2
    exit 1
fi
# claude 认证失败会把错误打到 stdout 并以 0 退出——不拦就把错误当草稿。
if head -5 "$CLAUDE_OUT" | grep -qiE "Failed to authenticate|API Error|authentication_error|Invalid API key"; then
    echo "CLAUDE_AUTH_ERROR:输出是错误信息不是草稿。" >&2
    head -5 "$CLAUDE_OUT" >&2
    rm -f "$CLAUDE_OUT"
    exit 1
fi

# 4) assemble:落 outbox 文章 + 通知,登记台账(草稿),独立回读
if ! python3 scripts/j1_runner.py --assemble "$CLAUDE_OUT" --commit \
        > "logs/j1_assemble_${STAMP}.json" 2>&1; then
    echo "ASSEMBLE_FAILED(含台账回读不一致)" >&2
    tail -40 "logs/j1_assemble_${STAMP}.json" >&2
    exit 1
fi

# 5) 只 PUSH 通知消息,不 PUSH 文章本体——文章长,群里发摘要与路径,正文去 outbox/台账看。
for f in outbox/j1_notify_${STAMP}_*.md; do
    [ -e "$f" ] || continue
    echo "PUSH: $(pwd)/$f"
done

# 被拒通知(assemble 产,有被拒才有)。一篇都没成稿时,这就是本轮唯一的实质消息——
# 08-12 那轮两条全被拒、群里一个字都没有,就是因为上面那个循环找不到文件。
#
# ⚠️ 必须显式 exit 0:`[ -s 文件 ] && echo` 在文件不存在时整条返回 1,
# 而它是脚本最后一条命令,于是一轮完全成功的运行会以退出码 1 收尾
# (2026-08-12 实测踩到:3 篇全部成稿、台账回读全 True,RC 仍是 1)。
# 执行体按退出码判成败,这会把成功报成失败。
if [ -s "outbox/j1_refused_${STAMP}.md" ]; then
    echo "PUSH: $(pwd)/outbox/j1_refused_${STAMP}.md"
fi
exit 0
