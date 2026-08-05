#!/usr/bin/env python3
"""AEO Engine · J4 —— Apollo sequence 回流轮询，回复出现时出通话邀约草稿。

依据：Build Spec · Phase 3「J4 冷 outreach 闭环：回流触发 24 小时内约 15 分钟通话的
      回复草稿，确认那封真人发；回流轮询自本 job 启动起才运行」、
      §J4 补充「回流轮询每日一次，随 daily_sla 之后运行」。

「自本 job 启动起才运行」的实现：首次运行落一个起点时间戳到
data/reply_poll_since.json，之后只看这个时间点之后的回复。
不这么做的话，第一次跑会把 Apollo 里历史上所有回复全捞出来当新回复处理。

零发送红线：本脚本只把草稿写进 outbox，由 sales agent 推群。**回信永远是真人发的。**
脚本不调用任何 Apollo 的发送接口。

计费：本脚本只读 emailer 消息，不富化、不解锁邮箱，不消耗 people credit。
     仍按纪律给出 dry-run 预估与 --no-poll 关闭开关。

用法：
    python3 scripts/reply_poll.py                 # dry-run
    python3 scripts/reply_poll.py --commit        # 写 outbox 与 since 状态
    python3 scripts/reply_poll.py --no-poll       # 关闭开关
"""

import json
import os
import sys
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "reply_poll"


def apollo(session, cfg_scan, api_key, method, path, payload=None):
    url = cfg_scan["apollo"]["base_url"] + path
    headers = {cfg_scan["apollo"]["auth_header"]: api_key,
               "Content-Type": "application/json", "accept": "application/json"}
    resp = session.request(method, url, json=payload, headers=headers, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError("Apollo {} {} → HTTP {}：{}".format(
            method, path, resp.status_code, resp.text[:400]))
    return resp.json()


def load_since(path, now):
    """返回 (since_iso, 是否首次)。首次运行即把起点定在此刻。"""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("since"), False
    return now.isoformat(), True


def save_since(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"since": value,
                   "note": "回流轮询起点。spec：回流轮询自本 job 启动起才运行。"},
                  fh, ensure_ascii=False, indent=2)


def draft_message(reply, cfg, now):
    """回复草稿的骨架。

    刻意**不调 LLM**：这一封的内容只有一件事——约一个写死时长的通话。
    骨架固定，个性化那一句留给真人（他刚读完对方回了什么，比模型清楚）。
    多调一次 claude 只会让这封信更啰嗦，而且回复窗口只有 24 小时，等不起。
    """
    mins = cfg["reply_poll"]["call_minutes"]
    lines = [
        "📬 AEO · J4 冷 outreach 回流",
        "",
        "收到回复，{} 小时内该回。".format(cfg["reply_poll"]["respond_within_hours"]),
        "",
        "对方：{}".format(reply.get("from_name") or reply.get("from_email") or "(未知)"),
        "邮箱：{}".format(reply.get("from_email") or "(未知)"),
        "sequence：{}".format(reply.get("sequence_name") or "(未知)"),
        "回复时间：{}".format(reply.get("replied_at") or "(未知)"),
        "",
        "—— 对方原文 ——",
        "",
        (reply.get("body") or "(取不到正文，请到 Apollo 界面看)").strip(),
        "",
        "—— 回复草稿（可直接复制）——",
        "",
        "Thanks for coming back to me.",
        "",
        "Easiest next step is {} minutes on a call — I'll run it on your own footage "
        "so you can judge it rather than take my word for it.".format(mins),
        "",
        "Does some time this week work?",
        "",
        "Shawn",
        "",
        "—— 以上 ——",
        "",
        "⚠️ 上面是骨架。发之前请按对方实际回了什么补一句针对性的回应——"
        "他刚说的话你比脚本清楚。这封由你本人发，脚本不发。",
    ]
    return "\n".join(lines)


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--no-poll", dest="no_poll", action="store_true",
                        help="关闭开关：整个轮询跳过，不调 Apollo")
    args = parser.parse_args()

    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        cfg = ac.load_config("outreach.yaml")
        cfg_scan = ac.load_config("scan.yaml")
        rp = cfg["reply_poll"]
        mode = sc.resolve_mode(args)

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        now = datetime.now(tz)

        if args.no_poll or not rp.get("enabled"):
            sc.emit(SCRIPT, {"script": SCRIPT, "status": "disabled",
                             "flag": rp["disable_flag"],
                             "enabled": rp.get("enabled"),
                             "wrote_outbox": False}, th)
            return 0

        api_key = env.get("APOLLO_API_KEY")
        if not api_key:
            sc.missing_credential(SCRIPT, "APOLLO_API_KEY", "回流轮询需要 Apollo API key", th)

        since_path = os.path.join(ac.REPO, rp["since_state_file"])
        since, first_run = load_since(since_path, now)

        session = requests.Session()

        # 本阶段只建了 A 段一条 sequence。只轮询我们自己建的，不扫别人的。
        camps = apollo(session, cfg_scan, api_key, "POST", "/emailer_campaigns/search",
                       {"q_name": cfg["sequence"]["naming"].split("_")[0],
                        "page": 1, "per_page": 100})
        ours = [c for c in (camps.get("emailer_campaigns") or [])
                if (c.get("name") or "").startswith("seq_")]

        replied_total = sum(int(c.get("unique_replied") or 0) for c in ours)

        drafts, notes = [], []
        if first_run:
            notes.append(
                "首次运行：把轮询起点定在 {}。此刻之前的回复一律不处理"
                "（spec：回流轮询自本 job 启动起才运行）。".format(since))

        if not ours:
            notes.append("没有找到任何 seq_ 开头的 sequence，无轮询对象。")
        elif replied_total == 0:
            notes.append(
                "{} 条 sequence 全部 unique_replied = 0，无回流。"
                "这是预期状态：sequence 建成即暂停，一封都没发出去，"
                "没发就不可能有回复。".format(len(ours)))
        else:
            # 有回复才去拉消息明细——没回复时拉一遍纯属浪费调用额度
            msgs = apollo(session, cfg_scan, api_key, "POST", "/emailer_messages/search",
                          {"emailer_campaign_ids": [c["id"] for c in ours],
                           "reply_class": "all", "page": 1, "per_page": 100})
            by_id = {c["id"]: c.get("name") for c in ours}
            for m in (msgs.get("emailer_messages") or []):
                replied_at = m.get("replied_at") or m.get("last_replied_at")
                if not replied_at or (since and replied_at <= since):
                    continue
                drafts.append({
                    "message_id": m.get("id"),
                    "from_name": m.get("to_name") or m.get("contact_name"),
                    "from_email": m.get("to_email") or m.get("email"),
                    "sequence_name": by_id.get(m.get("emailer_campaign_id")),
                    "replied_at": replied_at,
                    "body": (m.get("reply_body_text") or m.get("body_text") or "")[:2000],
                })

        written = []
        for d in drafts:
            fname = "j4_reply_{}_{}.md".format(
                now.strftime("%Y-%m-%d"), str(d["message_id"])[:8])
            if mode == "commit":
                ac.write_outbox(fname, draft_message(d, cfg, now))
            written.append({"file": fname, "from": d["from_email"],
                            "sequence": d["sequence_name"]})

        if mode == "commit":
            # 起点只在 commit 时推进。dry-run 推进起点会让真实回复被永久跳过。
            save_since(since_path, now.isoformat())

        result = {
            "script": SCRIPT, "mode": mode, "generated_at": now.isoformat(),
            "since": since, "first_run": first_run,
            "sequences_polled": [{"id": c["id"], "name": c.get("name"),
                                  "active": c.get("active"),
                                  "unique_delivered": c.get("unique_delivered"),
                                  "unique_replied": c.get("unique_replied")}
                                 for c in ours],
            "replies_new": len(drafts),
            "drafts": written,
            "notes": notes,
            "cost_estimate": {
                "people_credits_consumed": 0,
                "why": rp["billing"]["note"],
                "api_calls": 1 + (1 if replied_total else 0),
            },
            "wrote_outbox": mode == "commit" and bool(written),
        }
        sc.emit(SCRIPT, result, th)

        for w in written:
            print("PUSH: {}".format(os.path.join(ac.OUTBOX_DIR, w["file"])))
        if not written:
            print("NO_REPLIES：无新回流，不写 outbox，不推送。", file=sys.stderr)
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
