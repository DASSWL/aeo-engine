#!/usr/bin/env python3
"""AEO Engine · 每日 10:00 简报 —— 今天要干什么。

依据：运行手册页「📖 AEO Engine v1」的「一、运行时刻表」与「二、需要你做的事」。
    https://app.notion.com/p/3b3059d969338114a498df06ba197332

**零 LLM。** 拼装任务不需要生成：确定性和零额度消耗比漂亮措辞重要，而且这条任务
兼任心跳——它必须在 claude 登录态失效、API 额度耗尽时照样跑得出来。
脚本内不出现任何一句文案、任何一个业务数字，全部来自 config/brief.yaml。

四节，顺序固定：
    ① 今天机器会跑什么   —— 按 ISO 星期过滤 config 的时刻表
    ② 你今天的动作       —— 固定三件 + 动态数字（为 0 即整行省略）
    ③ 本周节点           —— 距下一个周批扫 / 周五复盘还有几天
    ④ 挂账清单链接       —— 只给链接，不解析其内容

与既有脚本的关系（本脚本一行都没改它们）：
    aeo_common     —— .env / config / Notion 客户端 / 字段取值，全部复用
    draft_runner   —— 复用 row_window 与 load_cooldown。「这一行什么时候到期」
                      必须与 draft_runner、sla_check 给同一个答案，所以不另写一份
    sla_check      —— 超时数**不重算**，直接读它 08:00 留下的 logs/sla_<date>.json。
                      重算等于两份实现必然漂移；读日志还顺带把「08:00 没跑」暴露出来

简报不含超时详情、不含草稿正文，只给计数和指引：08:00 与 08:30 已各司其职，
简报重复展开就是三份噪音。

用法：
    python3 scripts/daily_brief.py                    # 取数、渲染、写 outbox
    python3 scripts/daily_brief.py --stdout-only      # 只打到 stdout，不碰 outbox
    python3 scripts/daily_brief.py --as-of 2026-08-10 --sample --stdout-only
                                                      # 渲染某个星期几的形态样例

退出码：0 成功；1 失败（失败时仍会写出一条**失败简报**，见 config 的 failure 节）。
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import draft_runner as dr    # noqa: E402

SCRIPT = "daily_brief"
OUTBOX_TPL = "brief_{}.md"


# --------------------------------------------------------------------------
# 时刻表
# --------------------------------------------------------------------------

def todays_entries(cfg, isoweekday):
    """当天要跑的自动任务，日常与周节点合并后按 sort 排序。"""
    sched = cfg["schedule"]
    entries = [e for e in (sched.get("daily") or []) if isoweekday in e["days"]]
    entries += [e for e in (sched.get("weekly") or []) if isoweekday in e["days"]]
    return sorted(entries, key=lambda e: e["sort"])


def days_until(isoweekday, target_weekday):
    """今天到下一个 target_weekday 还有几天。今天就是 → 0。"""
    return (target_weekday - isoweekday) % 7


# --------------------------------------------------------------------------
# 动态数字
#
# 四个数字各有各的出处，出处不同是有意的：
#   待发草稿   —— data/draft_runner_sent.json ∩ 水箱仍在待触达状态的行
#   inbox      —— 水箱 状态 = inbox
#   台账待签发 —— 台账 状态 = 草稿
#   超时       —— 08:00 的 logs/sla_<date>.json，本脚本不重算
# 字段名逐字取自 Phase 0 核对清单：状态 / 人名 / 公司 / 来源 / 信号类型 /
# 入箱日期 / 触达时限起算。
# --------------------------------------------------------------------------

def count_by_status(rows, value):
    return sum(1 for r in rows
               if ac.select_name(r.get("properties", {}), "状态") == value)


def pending_drafts(pipeline, outreach_cfg, sla, tz, now):
    """已推过草稿、但水箱行还停在待触达状态的行 —— 也就是「你还没发出去的」。

    出处是 draft_runner 写的冷却状态文件：它记的正是「哪一行在哪一刻被推过草稿」。
    回执处理器把发出去的行改成「触达中」，因此「推过 且 仍是 inbox/Named」
    就等价于「草稿在群里躺着，你还没发」。

    不按冷却期截断：躺了三天没发的草稿依然是待发的，截掉只会让积压看不见。

    返回 (条数, 最紧一条 or None)。最紧 = 剩余时限最小（负数即已逾期，排最前）。
    """
    q = outreach_cfg["queue"]
    pushed = dr.load_cooldown()
    if not pushed:
        return 0, None

    by_id = {r.get("id"): r for r in pipeline}
    items = []
    for row_id in pushed:
        row = by_id.get(row_id)
        if row is None:
            continue  # 行被删/归档：不猜，不计数
        props = row.get("properties", {})
        if ac.select_name(props, "状态") not in q["statuses"]:
            continue  # 已触达 / 已转化 / 已休眠 —— 不再待发
        base, hours, _why = dr.row_window(props, q, sla, tz)
        remaining = None
        if base is not None:
            remaining = ((base + timedelta(hours=hours)) - now).total_seconds() / 3600.0
        items.append({
            "人名": ac.title_text(props, "人名"),
            "公司": ac.rich_text(props, "公司"),
            "remaining_hours": remaining,
        })

    if not items:
        return 0, None
    # 算不出窗口的行排最后（None 不参与「最紧」评比，但照样计数）
    ranked = sorted(items, key=lambda i: (i["remaining_hours"] is None,
                                          i["remaining_hours"]))
    return len(items), ranked[0]


def overdue_count(day_str, data_cfg):
    """读 08:00 sla_check 留下的当日结果。返回 (计数, 文件是否存在)。"""
    path = os.path.join(ac.LOGS_DIR,
                        data_cfg["sla_log_template"].format(date=day_str))
    if not os.path.exists(path):
        return None, False
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get(data_cfg["sla_total_key"]), True


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def render(cfg, now, isoweekday, numbers):
    r = cfg["render"]
    s = cfg["sections"]
    lines = [r["header"].format(
        date=now.strftime("%Y-%m-%d"),
        weekday=r["weekday_names"][isoweekday - 1])]

    # ---- ① 今天机器会跑什么 ----
    entries = todays_entries(cfg, isoweekday)
    m = s["machine"]
    lines += ["", m["title"]]
    if entries:
        lines += [m["line"].format(**e) for e in entries]
    else:
        lines.append(m["empty"])

    # ---- ② 你今天的动作 ----
    a = s["actions"]
    d = a["dynamic"]
    lines += ["", a["title"]]
    lines += list(a["fixed"])

    # 为 0 即整行省略（固定三件除外）。开关在 config，不在这里。
    omit_zero = r["omit_zero_rows"]

    def show(n):
        return bool(n) or not omit_zero

    n_drafts, urgent = numbers["drafts"]
    if show(n_drafts):
        dp = d["drafts_pending"]
        lines.append(dp["line"].format(n=n_drafts))
        if urgent:
            rem = urgent["remaining_hours"]
            if rem is None:
                remaining = ""
            elif rem < 0:
                remaining = dp["remaining_overdue"].format(h=abs(round(rem, 1)))
            else:
                remaining = dp["remaining_left"].format(h=round(rem, 1))
            if remaining:
                lines.append(dp["urgent_line"].format(
                    name=urgent["人名"] or dp["unknown_name"],
                    company=urgent["公司"] or dp["unknown_company"],
                    remaining=remaining))

    if show(numbers["inbox"]):
        lines.append(d["inbox_pending"]["line"].format(n=numbers["inbox"]))
    if show(numbers["ledger"]):
        lines.append(d["ledger_pending"]["line"].format(n=numbers["ledger"]))

    n_overdue, sla_present = numbers["overdue"]
    if not sla_present:
        # 08:00 没留下结果 ≠ 今天没有超时。这一行属于心跳，不参与省略规则。
        lines.append(d["overdue"]["missing"])
    elif show(n_overdue):
        lines.append(d["overdue"]["line"].format(n=n_overdue))

    # ---- ③ 本周节点 ----
    w = s["week"]
    frags = []
    for e in (cfg["schedule"].get("weekly") or []):
        for target in e["days"]:
            n = days_until(isoweekday, target)
            when = w["today_word"] if n == 0 else w["future_word"].format(n=n)
            frags.append(w["item"].format(label=e["label"], when=when))
    if frags:
        lines += ["", w["title"], w["joiner"].join(frags)]

    # ---- ④ 挂账清单 ----
    t = s["tail"]
    lines += ["", t["line"].format(url=t["url"])]

    return enforce_cap(lines, r)


def enforce_cap(lines, r):
    """行数上限。先压空行，仍超限才截断——截断必须看得见。"""
    cap = r["max_lines"]
    if len(lines) <= cap:
        return lines, len(lines), False
    compacted = [ln for ln in lines if ln.strip()]
    if len(compacted) <= cap:
        print("超过 {} 行上限，已压掉空行（{} → {}）。".format(
            cap, len(lines), len(compacted)), file=sys.stderr)
        return compacted, len(compacted), False
    kept = compacted[:cap - 1] + [r["overflow_marker"].format(max=cap)]
    print("超过 {} 行上限，压空行后仍为 {} 行，已截断。".format(
        cap, len(compacted)), file=sys.stderr)
    return kept, len(kept), True


def render_failure(cfg, now, isoweekday, error_text):
    """失败简报。简报每天必发——出错也得推得出去一条。"""
    r = cfg["render"]
    f = cfg["failure"]
    lines = [f["header"].format(
        date=now.strftime("%Y-%m-%d"),
        weekday=r["weekday_names"][isoweekday - 1])]
    for ln in f["body"]:
        lines.append(ln.format(stamp=now.strftime("%Y-%m-%d %H:%M %Z"),
                               error=error_text))
    return lines


# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--stdout-only", dest="stdout_only", action="store_true",
                   help="只打到 stdout，不写 outbox（本地看形态用）")
    p.add_argument("--as-of", dest="as_of", default=None,
                   help="按指定日期渲染（YYYY-MM-DD），用于看周一/周五/周末形态")
    p.add_argument("--sample", action="store_true",
                   help="配合 --as-of：日历按 --as-of 走，数字仍取今天的真实数据")
    args = p.parse_args()

    # 时区与「今天」先算出来——失败简报也要用它们，所以放在 try 外面，
    # 且只依赖 thresholds.yaml 这一处。这一步都能挂的话，config 目录本身就没了。
    cfg = None
    try:
        th = ac.load_config("thresholds.yaml")
        tz = ZoneInfo(th["week_window"]["timezone"])
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("America/Los_Angeles")
    now = datetime.now(tz)

    try:
        cfg = ac.load_config("brief.yaml")
        th = ac.load_config("thresholds.yaml")
        outreach = ac.load_config("outreach.yaml")

        if args.as_of:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").replace(tzinfo=tz)
            render_day = as_of
        else:
            render_day = now
        isoweekday = render_day.isoweekday()

        env = ac.load_env()
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        pipeline = notion.query_all(env["DS_PIPELINE"])
        ledger = notion.query_all(env["DS_LEDGER"])

        # --sample 时超时数仍取今天的真实结果：形态样例要像真的，
        # 否则三份样例全挂着「08:00 没留下结果」，看不出正常形态长什么样。
        sla_day = now if (args.sample and args.as_of) else render_day
        data_cfg = cfg["data"]
        numbers = {
            "drafts": pending_drafts(pipeline, outreach, th["sla"], tz, now),
            "inbox": count_by_status(pipeline, data_cfg["pipeline_inbox_status"]),
            "ledger": count_by_status(ledger, data_cfg["ledger_draft_status"]),
            "overdue": overdue_count(sla_day.strftime("%Y-%m-%d"), data_cfg),
        }

        lines, line_count, truncated = render(cfg, render_day, isoweekday, numbers)
        body = "\n".join(lines)
        if not body.strip():
            raise RuntimeError(cfg["failure"]["empty_render"])

        print(body)
        if not args.stdout_only:
            path = ac.write_outbox(
                OUTBOX_TPL.format(render_day.strftime("%Y-%m-%d")), body + "\n")
            print("写入 {}（{} 行{}）".format(
                path, line_count, "，已截断" if truncated else ""), file=sys.stderr)
        return 0

    except Exception as exc:  # noqa: BLE001
        # 静默就是故障：这里**不能**只报错了事，必须留下一条推得出去的失败简报。
        detail = "{}: {}".format(type(exc).__name__, exc)
        print(traceback.format_exc(), file=sys.stderr)
        try:
            fcfg = cfg or ac.load_config("brief.yaml")
            lines = render_failure(fcfg, now, now.isoweekday(), detail)
        except Exception:  # noqa: BLE001
            # 连 brief.yaml 都读不到：最后一道兜底，宁可丑也要有内容。
            lines = ["⚠️ AEO Engine · 今日简报生成失败", "", detail]
        body = "\n".join(lines)
        print(body)
        if not args.stdout_only:
            try:
                ac.write_outbox(
                    OUTBOX_TPL.format(now.strftime("%Y-%m-%d")), body + "\n")
            except Exception:  # noqa: BLE001
                pass  # 写不了文件时由 run_daily_brief.sh 兜最后一层
        return 1


if __name__ == "__main__":
    sys.exit(main())
