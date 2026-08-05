#!/usr/bin/env python3
"""AEO Engine · J4 确认回执处理器。

出处：Build Spec · Phase 3 §J4 补充「确认回执协议：Shawn 在绑定 group（chat ID
      -5261250225）里回复「sent 行ID」→ sales agent 按规则更新该水箱行状态为触达中
      并写时间戳；只认 Shawn 账号发出的确认，群内其他消息一律忽略」。

为什么是个脚本而不是让 agent 直接改 Notion：
  写库必须显式 --commit、必须先能 dry-run 看一遍（Phase 2 立的规矩）。
  让 agent 临场拼 Notion PATCH 请求，等于把「改真库」这件事交给一次自由发挥，
  而且 agent 每次拼的字段可能都不一样。这里把它固化成一条确定路径。

三道校验，任一不过即拒绝（退出码非 0），不静默改库：
  1. 回执语法必须匹配 config/outreach.yaml 的 receipt.pattern
  2. 发送者 Telegram 数字 ID 必须在 allow_from_telegram_user_ids 里
  3. 目标行必须存在、必须在水箱库里、状态必须是 inbox / Named 之一
     （已经是「触达中」的行重复确认 → 幂等跳过，不重复盖时间戳）

用法：
    python3 scripts/receipt_apply.py --row <id> --from <telegram_user_id>
    python3 scripts/receipt_apply.py --row <id> --from <id> --commit
    python3 scripts/receipt_apply.py --text "sent 3b30...f48" --from 6529461266 --commit
退出码：0 成功/幂等跳过；1 执行失败；4 校验不通过（拒绝）
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "receipt_apply"
EXIT_REJECTED = 4


def normalize_id(raw):
    """Notion page id 带不带连字符都认，统一成带连字符的 8-4-4-4-12。"""
    h = (raw or "").replace("-", "").strip().lower()
    if len(h) != 32 or not re.fullmatch(r"[0-9a-f]{32}", h):
        return None
    return "{}-{}-{}-{}-{}".format(h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--row", default=None, help="水箱行 ID")
    parser.add_argument("--text", default=None, help="整条回执原文，按 pattern 解析")
    parser.add_argument("--from", dest="sender", required=True,
                        help="发送者的 Telegram 数字 user id")
    args = parser.parse_args()

    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        cfg = ac.load_config("outreach.yaml")
        r = cfg["receipt"]

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        now = datetime.now(tz)

        def reject(reason, extra=None):
            out = {"script": SCRIPT, "status": "rejected", "reason": reason,
                   "wrote_notion": False, "checked_at": now.isoformat()}
            if extra:
                out.update(extra)
            sc.emit(SCRIPT, out, th)
            sys.exit(EXIT_REJECTED)

        if not r.get("enabled"):
            reject("receipt.enabled = false，回执协议未启用")

        # ---- 校验 1：发送者 ----
        # 只认 Shawn 账号。群内其他人发一模一样的字符串也不算数。
        try:
            sender = int(str(args.sender).strip())
        except ValueError:
            reject("发送者 ID 不是数字：{}".format(args.sender))
        allowed = r["allow_from_telegram_user_ids"]
        if sender not in allowed:
            reject("发送者 {} 不在白名单 {} 中，忽略。".format(sender, allowed),
                   {"sender": sender})

        # ---- 校验 2：语法 ----
        if args.text:
            m = re.match(r["pattern"], args.text)
            if not m:
                reject("回执语法不匹配 pattern：{!r}".format(args.text))
            raw_row = m.group(1)
        elif args.row:
            raw_row = args.row
        else:
            reject("必须给 --row 或 --text")

        row_id = normalize_id(raw_row)
        if not row_id:
            reject("行 ID 不是合法的 Notion page id：{!r}".format(raw_row))

        # ---- 校验 3：目标行 ----
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        page = notion.get_page(row_id)
        parent = page.get("parent") or {}
        ds_id = (parent.get("data_source_id") or parent.get("database_id") or "")
        want = {env["DS_PIPELINE"].replace("-", ""), env["DB_PIPELINE"].replace("-", "")}
        if ds_id.replace("-", "") not in want:
            reject("目标行不在水箱库里（parent={}）。回执只能改水箱行。".format(ds_id),
                   {"row": row_id})

        props = page.get("properties", {})
        name = ac.title_text(props, "人名")
        status = ac.select_name(props, "状态")
        target = r["on_match"]["set_status"]

        if status == target:
            # 幂等：重复确认不重复盖时间戳，否则时限起算点会被一路往后推
            out = {"script": SCRIPT, "status": "already_applied",
                   "row": row_id, "人名": name, "现状态": status,
                   "wrote_notion": False}
            sc.emit(SCRIPT, out, th)
            return 0

        if status not in cfg["queue"]["statuses"]:
            reject("行状态为「{}」，不在可确认状态 {} 内。".format(
                status, cfg["queue"]["statuses"]), {"row": row_id, "人名": name})

        # ---- 应用 ----
        next_action = ac.rich_text(props, "下一步动作")
        note = r["on_match"]["append_next_action"]
        merged = "{}｜{} {}".format(next_action, note, now.strftime("%Y-%m-%d %H:%M %Z")) \
            if next_action else "{} {}".format(note, now.strftime("%Y-%m-%d %H:%M %Z"))

        payload = {
            "状态": sc.p_select(target),
            r["on_match"]["stamp_field"]: sc.p_date(now.isoformat()),
            "下一步动作": sc.p_text(merged),
        }

        result = {
            "script": SCRIPT, "mode": sc.resolve_mode(args), "row": row_id,
            "人名": name, "公司": ac.rich_text(props, "公司"),
            "原状态": status, "新状态": target,
            "stamp_field": r["on_match"]["stamp_field"],
            "stamp_value": now.isoformat(),
            "sender": sender,
            "wrote_notion": False,
        }

        if sc.resolve_mode(args) == "commit":
            notion.update_page(row_id, payload)
            # 回读核对，不看自己的回执（Phase 2 立的规矩：重新拉线上数据）
            back = notion.get_page(row_id)
            bp = back.get("properties", {})
            result["wrote_notion"] = True
            result["readback"] = {
                "状态": ac.select_name(bp, "状态"),
                r["on_match"]["stamp_field"]: str(
                    ac.date_prop(bp, r["on_match"]["stamp_field"], tz)),
            }
            result["readback_ok"] = ac.select_name(bp, "状态") == target

        sc.emit(SCRIPT, result, th)
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
