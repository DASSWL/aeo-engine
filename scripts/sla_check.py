#!/usr/bin/env python3
"""AEO Engine · 每日时限检查。

规则逐条来自 Build Spec · Phase 1 §四「sla_check.py 规则」一至四。
零 LLM。所有时限来自 config/thresholds.yaml 的 sla 节，脚本内无字面量。

J4 上线前的运行约定（spec §一.1）：触达完成由真人手动改状态并更新时间戳，
因此规则一、二只对状态为 inbox / Named 的行报警，避免 J4 缺位期间全量误报。

输出为空 → 退出码 0 且不写 outbox，OpenClaw 无内容即不推送。

用法：python3 scripts/sla_check.py
输出：outbox/sla_YYYY-MM-DD.md（仅在有超时项时）
"""

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac  # noqa: E402

SCRIPT = "sla_check"

# 出处：spec §一.1「SLA 检查只对状态为 inbox 或 Named 的行报警」
ACTIONABLE_STATUSES = ("inbox", "Named")
SIGNAL_TYPE = "signal"      # 规则一：信号类型 = signal
REFERRAL_SOURCE = "referral"  # 规则二：来源 = referral
LEDGER_DRAFT = "草稿"        # 规则四：状态 = 草稿


def page_url(page):
    return page.get("url") or ""


def main():
    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        sla = th["sla"]
        tz = ZoneInfo(th["week_window"]["timezone"])
        now = datetime.now(tz)

        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        pipeline = notion.query_all(env["DS_PIPELINE"])
        winloss = notion.query_all(env["DS_WINLOSS"])
        ledger = notion.query_all(env["DS_LEDGER"])

        r1, r2, r3, r4 = [], [], [], []

        # ---- 规则一：信号 N 小时未触达 ----
        # 状态 inbox/Named，信号类型 = signal，现在 − 触达时限起算 > signal_hours
        limit1 = timedelta(hours=sla["signal_hours"])
        for row in pipeline:
            p = row.get("properties", {})
            if ac.select_name(p, "状态") not in ACTIONABLE_STATUSES:
                continue
            if ac.select_name(p, "信号类型") != SIGNAL_TYPE:
                continue
            started = ac.date_prop(p, "触达时限起算", tz)
            if started and now - started > limit1:
                r1.append({
                    "人名": ac.title_text(p, "人名"),
                    "公司": ac.rich_text(p, "公司"),
                    "segment": ac.select_name(p, "segment"),
                    "状态": ac.select_name(p, "状态"),
                    "超时": round((now - started).total_seconds() / 3600, 1),
                    "url": page_url(row),
                })

        # ---- 规则二：referral N 小时未触达 ----
        # 来源 = referral，状态 inbox/Named，现在 − 入箱日期 > referral_hours
        limit2 = timedelta(hours=sla["referral_hours"])
        for row in pipeline:
            p = row.get("properties", {})
            if ac.select_name(p, "来源") != REFERRAL_SOURCE:
                continue
            if ac.select_name(p, "状态") not in ACTIONABLE_STATUSES:
                continue
            inboxed = ac.date_prop(p, "入箱日期", tz)
            if inboxed and now - inboxed > limit2:
                r2.append({
                    "人名": ac.title_text(p, "人名"),
                    "公司": ac.rich_text(p, "公司"),
                    "segment": ac.select_name(p, "segment"),
                    "超时": round((now - inboxed).total_seconds() / 3600, 1),
                    "url": page_url(row),
                })

        # ---- 规则三：win/loss 未按时填写 ----
        # 分两类：已填但迟填（spec 明文「已补填的也报一次，标注迟填」）；
        # 以及对话已过时限但仍未填（交付物 §1「对话 24 小时未填 win/loss」）。
        fill_days = sla["winloss_fill_days"]
        for row in winloss:
            p = row.get("properties", {})
            talk = ac.date_prop(p, "日期", tz)
            fill = ac.date_prop(p, "填写日期", tz)
            if not talk:
                continue
            if fill:
                late = (fill - talk).days
                if late > fill_days:
                    r3.append({
                        "对话标识": ac.title_text(p, "对话标识"),
                        "公司": ac.rich_text(p, "公司"),
                        "类型": "迟填",
                        "延迟天数": late,
                        "url": page_url(row),
                    })
            elif (now - talk).days > fill_days:
                r3.append({
                    "对话标识": ac.title_text(p, "对话标识"),
                    "公司": ac.rich_text(p, "公司"),
                    "类型": "未填",
                    "延迟天数": (now - talk).days,
                    "url": page_url(row),
                })

        # ---- 规则四：台账草稿积压 ----
        limit4 = timedelta(hours=sla["ledger_draft_hours"])
        for row in ledger:
            p = row.get("properties", {})
            if ac.select_name(p, "状态") != LEDGER_DRAFT:
                continue
            created = ac.date_prop(p, "创建日期", tz)
            if created and now - created > limit4:
                r4.append({
                    "资产名": ac.title_text(p, "资产名"),
                    "类型": ac.select_name(p, "类型"),
                    "超时": round((now - created).total_seconds() / 3600, 1),
                    "url": page_url(row),
                })

        total = len(r1) + len(r2) + len(r3) + len(r4)
        report = {
            "generated_at": now.isoformat(),
            "thresholds_used": sla,
            "row_counts_read": {"pipeline": len(pipeline),
                                "winloss": len(winloss),
                                "ledger": len(ledger)},
            "total_overdue": total,
            "rule_1_signal_overdue": r1,
            "rule_2_referral_overdue": r2,
            "rule_3_winloss_fill": r3,
            "rule_4_ledger_draft_backlog": r4,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))

        if total == 0:
            print("无超时项：不写 outbox，不推送。", file=sys.stderr)
            sys.exit(0)

        ledger_db = env["DB_LEDGER"].replace("-", "")
        lines = ["⏰ AEO Engine · 时限检查 {}".format(now.strftime("%Y-%m-%d %H:%M %Z")),
                 "", "共 {} 项超时。".format(total), ""]
        if r1:
            lines.append("**① 信号超过 {} 小时未触达（{} 项）**".format(
                sla["signal_hours"], len(r1)))
            for i in r1:
                lines.append("- {}（{}｜{}）逾期 {}h · {}".format(
                    i["人名"] or "(无名)", i["公司"] or "-", i["segment"] or "-",
                    i["超时"], i["url"]))
            lines.append("")
        if r2:
            lines.append("**② referral 超过 {} 小时未触达（{} 项）**".format(
                sla["referral_hours"], len(r2)))
            for i in r2:
                lines.append("- {}（{}｜{}）逾期 {}h · {}".format(
                    i["人名"] or "(无名)", i["公司"] or "-", i["segment"] or "-",
                    i["超时"], i["url"]))
            lines.append("")
        if r3:
            lines.append("**③ win/loss 超过 {} 天未按时填写（{} 项）**".format(
                fill_days, len(r3)))
            for i in r3:
                lines.append("- [{}] {}（{}）延迟 {} 天 · {}".format(
                    i["类型"], i["对话标识"] or "(无标识)", i["公司"] or "-",
                    i["延迟天数"], i["url"]))
            lines.append("")
        if r4:
            lines.append("**④ 台账草稿超过 {} 小时未签发（{} 项）**".format(
                sla["ledger_draft_hours"], len(r4)))
            for i in r4:
                lines.append("- {}（{}）积压 {}h · {}".format(
                    i["资产名"] or "(无名)", i["类型"] or "-", i["超时"], i["url"]))
            lines.append("")
        lines.append("台账「待签发」视图：https://app.notion.com/p/{}".format(ledger_db))

        path = ac.write_outbox("sla_{}.md".format(now.strftime("%Y-%m-%d")),
                               "\n".join(lines))
        print("写入 {}".format(path), file=sys.stderr)
        sys.exit(0)

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    main()
