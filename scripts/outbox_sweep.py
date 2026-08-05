#!/usr/bin/env python3
"""AEO Engine · outbox 兜底转发器。

存在的理由（2026-08-05 发现的架构缺口）：

  系统里有两类产出者，投递能力完全不同：
    * OpenClaw cron 任务 —— 跑 run_*.sh，把 PUSH: 路径交给 sales agent 转发 ✅
    * Claude scheduled task（probe_daily / linkedin_daily / linkedin_reddit_weekly）
      —— 只能写 outbox/，**没有 Telegram 工具**，够不着群 ❌

  而四个 OpenClaw 任务各自只读自己那个脚本的 stdout，没有任何一个扫 outbox/ 目录。
  结果：Claude 侧任务写的每一份报告都躺在 outbox 里没人看。

  2026-08-05 实测：probe_daily 正确地在自检第 1 条停机（Perplexity 未登录），
  按 playbook §9 写了 outbox/probe_report_2026-08-05.md，报告详尽且判断正确——
  但它在 outbox 里躺了一整天，直到真人主动问「探测库为什么 0 行」才被发现。
  **失败被正确检测到了，却没有被送达。** 这比没检测更危险：它看起来像什么都没发生。

本脚本每天扫一遍 outbox，把**没有归属**的报告文件转发出去。
已被其他任务认领的文件（J4 草稿、sla、复盘包、简报）跳过，避免重复推送。

幂等：转发过的文件记进 data/outbox_forwarded.json，不重复发。
默认 dry-run，--commit 才写已转发记录。

用法：
    python3 scripts/outbox_sweep.py            # 看会转发什么，不落记录
    python3 scripts/outbox_sweep.py --commit   # 输出 PUSH: 并记录已转发
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "outbox_sweep"
STATE = "outbox_forwarded.json"

# 已有归属的文件前缀 —— 这些由各自的 OpenClaw 任务转发，本脚本不碰，否则会重复推送。
# 加新任务时记得同步这张表，漏加会导致同一份内容推两次。
CLAIMED_PREFIXES = (
    "j4_draft_",     # j4_draft_runner
    "j4_reply_",     # j4_reply_poll
    "sla_",          # daily_sla
    "review.md",     # friday_review
    "brief_",        # daily_brief
)


def load_state():
    path = os.path.join(ac.DATA_DIR, STATE)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state):
    path = os.path.join(ac.DATA_DIR, STATE)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    args = parser.parse_args()

    try:
        th = ac.load_config("thresholds.yaml")
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        now = datetime.now(tz)

        state = load_state()
        if not os.path.isdir(ac.OUTBOX_DIR):
            sc.emit(SCRIPT, {"script": SCRIPT, "status": "no_outbox",
                             "forwarded": []}, th)
            return 0

        to_send, skipped = [], []
        for name in sorted(os.listdir(ac.OUTBOX_DIR)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(ac.OUTBOX_DIR, name)
            if name.startswith(CLAIMED_PREFIXES) or name == "review.md":
                skipped.append({"file": name, "why": "已有归属任务转发"})
                continue
            if name in state:
                skipped.append({"file": name, "why": "已转发于 {}".format(state[name])})
                continue
            to_send.append({"file": name, "path": path,
                            "bytes": os.path.getsize(path)})

        result = {
            "script": SCRIPT, "mode": sc.resolve_mode(args),
            "generated_at": now.isoformat(),
            "unclaimed_new": len(to_send),
            "forwarded": to_send,
            "skipped": skipped,
        }

        if sc.resolve_mode(args) == "commit":
            for item in to_send:
                state[item["file"]] = now.isoformat()
            save_state(state)

        sc.emit(SCRIPT, result, th)

        for item in to_send:
            print("PUSH: {}".format(item["path"]))
        if not to_send:
            print("NO_UNCLAIMED：outbox 里没有待转发的无归属报告。", file=sys.stderr)
        return 0

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
