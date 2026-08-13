#!/usr/bin/env python3
"""AEO Engine · A1 感知层观测报告。

补的是 2026-08-05 挂账的三条，它们本质是同一件事：
**A1 的产出目前不可观测，而 J0 的砍留 segment 判据完全建立在它之上。**

  ① 填补速度判据失真 —— metrics.py 的 weekly_inbox 把 Apollo 与扫描混在一列。
     Apollo 对任何 segment 都能捞到几十万人（A 段实测匹配 326,946），
     它入箱多少取决于 caps.per_segment_per_round 这个我们自己设的上限，
     **不取决于 segment 的信号密度**。拿混合数排名，等于拿我设的上限当证据。
  ② 探测无成功判据 —— scan_log 的字段是 {hits, inboxed, skipped_dupe}，全是入水箱口径。
     而探测按设计不写水箱，它的 hits 永远是结构性的 0。于是「跑完写了 30 条」、
     「卡自检停机」、「压根没触发」三种情况在日志里长得一模一样。
  ③ signal_hit_rate 分母是累计的 —— metrics.py 把所有 scan_*.json 的 by_segment
     无窗口过滤地累加（Phase 2 §八 已记，属四个待拍板问题之一）。
     分子按周、分母累计，跑得越久这个比率越假。

本脚本**不改 metrics.py**（硬约束：不动 Phase 1/2 的脚本；且 ③ 属明文保留的待拍板项）。
它旁挂计算，把修正值与 metrics.py 的原值并列呈现，差多少一眼可见。
要根治 ③ 只需在 metrics.py 的 scan 文件遍历处加一个窗口过滤，那是真人的决定。

用法：
    python3 scripts/a1_health.py              # 打印报告
    python3 scripts/a1_health.py --commit     # 同时写 outbox（由 outbox_sweep 转发）
"""

import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "a1_health"

# 来源分类。出处：Phase 0 冻结的水箱「来源」选项 A1 扫描 / Apollo / referral / 手动。
# 只有前者与 referral 反映「这个 segment 的公开信号密度」——那才是填补速度。
VELOCITY_SOURCES = ("A1 扫描", "referral")
BULK_SOURCES = ("Apollo",)

SCAN_DATE_RE = re.compile(r"scan_(\d{4}-\d{2}-\d{2})\.json$")


def fill_velocity(pipeline, window, tz, segments):
    """① 填补速度分来源。返回逐 segment 的窗口内入箱数，按来源拆开。"""
    out = {s: {"扫描侧": 0, "Apollo 侧": 0, "手动": 0, "合计": 0} for s in segments}
    for row in pipeline:
        p = row.get("properties", {})
        seg = ac.select_name(p, "segment")
        if seg not in out:
            continue
        inboxed = ac.date_prop(p, "入箱日期", tz)
        if not ac.in_window(inboxed, window):
            continue
        src = ac.select_name(p, "来源")
        if src in VELOCITY_SOURCES:
            out[seg]["扫描侧"] += 1
        elif src in BULK_SOURCES:
            out[seg]["Apollo 侧"] += 1
        else:
            out[seg]["手动"] += 1
        out[seg]["合计"] += 1
    return out


def probe_health(probe_rows, tz, window, expected_per_day):
    """② 探测健康度。用探测记录库自身的行数判定，不看 scan_log 的水箱口径。"""
    by_date = defaultdict(int)
    for row in probe_rows:
        d = ac.date_prop(row.get("properties", {}), "日期", tz)
        if d:
            by_date[d.strftime("%Y-%m-%d")] += 1
    in_window_days = sorted(d for d in by_date
                            if ac.in_window(
                                datetime.fromisoformat(d).replace(tzinfo=tz), window))

    # 窗口内应该有数据、实际一行都没有的日子。
    # 2026-08-12 新增：原来只要窗口内任意一天有数据就判「窗口内有数据」，
    # 于是 7 天里跑成 2 天与跑成 7 天在这份报告里长得一模一样。
    # 08-11 的探测就是这么消失的——没有停机报告、没有日志行、什么都没有，
    # 而当周 a1_health 仍会说「窗口内有数据」。缺席必须点名，否则它等于没被发现。
    # 只判**整天都落在窗口内**的日子。窗口首日通常是半天（start 带时刻），
    # 而探测在 01:00 跑——那一场多半在窗口开始之前，算它缺席是误报。
    missing_days = []
    today = datetime.now(tz).strftime("%Y-%m-%d")
    cur = window["start"].replace(hour=0, minute=0, second=0, microsecond=0)
    if window["start"].time() != cur.time():
        cur += timedelta(days=1)          # 首日不完整，从下一个整天起算
    while cur + timedelta(days=1) <= window["end"]:
        key = cur.strftime("%Y-%m-%d")
        # 今天不算缺席：探测 01:00 跑，本报告可能跑在它之前。
        if key < today and key not in by_date:
            missing_days.append(key)
        cur += timedelta(days=1)

    if not probe_rows:
        verdict = "从未写入过任何探测记录"
    elif missing_days:
        # 成因分三种，本脚本只能报「零记录」，报不了为什么——credit 是桌面端
        # 订阅额度，仓库侧看不到。所以文案给的是排查顺序，不是结论：
        # 2026-08-11 那天就是 credit 用完，当时被记成了调度漏跑。
        verdict = ("⚠️ 窗口内有 {} 天零记录：{}。"
                   "有停机报告的看报告；没有报告的**先确认当周 credit 有没有用完**，"
                   "再查调度——这两种在仓库里长得一模一样").format(
            len(missing_days), "、".join(missing_days))
    elif in_window_days:
        verdict = "窗口内每天都有数据"
    else:
        verdict = "有历史数据但本窗口内没有"

    return {
        "expected_per_day": expected_per_day,
        "total_rows": len(probe_rows),
        "days_with_data_in_window": len(in_window_days),
        "by_date_in_window": {d: by_date[d] for d in in_window_days},
        "missing_days_in_window": missing_days,
        "verdict": verdict,
    }


def scan_denominator(window, tz):
    """③ 修正后的信号命中率分母：只算落在本周窗口内的 scan_*.json。

    对照 metrics.py 的口径（把全部历史文件无过滤累加），差值即失真量。
    """
    windowed, cumulative = defaultdict(int), defaultdict(int)
    files_in, files_all = [], []
    for path in sorted(glob.glob(os.path.join(ac.LOGS_DIR, "scan_*.json"))):
        m = SCAN_DATE_RE.search(os.path.basename(path))
        files_all.append(os.path.basename(path))
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        by_seg = doc.get("by_segment") or {}
        for seg, cnt in by_seg.items():
            if isinstance(cnt, int):
                cumulative[seg] += cnt
        if not m:
            continue
        d = datetime.fromisoformat(m.group(1)).replace(tzinfo=tz)
        if ac.in_window(d, window):
            files_in.append(os.path.basename(path))
            for seg, cnt in by_seg.items():
                if isinstance(cnt, int):
                    windowed[seg] += cnt
    return {
        "files_total": len(files_all), "files_in_window": len(files_in),
        "windowed": dict(windowed), "cumulative_as_metrics_py_sees_it": dict(cumulative),
        "drift": {seg: cumulative.get(seg, 0) - windowed.get(seg, 0)
                  for seg in set(cumulative) | set(windowed)},
        "note": ("扫描日志共 {} 个文件，其中 {} 个落在本窗口。"
                 "两个口径的差值即失真量：分子按周、分母累计，"
                 "跑满几周后 metrics.py 的 signal_hit_rate 会系统性偏低。"
                 ).format(len(files_all), len(files_in)),
    }


def render(result):
    v = result["fill_velocity"]
    ph = result["probe_health"]
    sd = result["scan_denominator"]
    L = ["📡 AEO · A1 感知层观测 {}".format(result["week_label"]),
         "", "窗口：{} → {}".format(result["window_start"][:16], result["window_end"][:16]), ""]

    L += ["**① 填补速度（分来源）**", "",
          "砍留 segment 的判据是填补速度，而只有扫描侧反映信号密度——",
          "Apollo 入箱多少取决于我们自己设的每轮上限，不取决于 segment 质地。", "",
          "| segment | 扫描侧 | Apollo 侧 | 手动 | 合计 |", "|---|---:|---:|---:|---:|"]
    for seg in sorted(v):
        r = v[seg]
        L.append("| {} | {} | {} | {} | {} |".format(
            seg, r["扫描侧"], r["Apollo 侧"], r["手动"], r["合计"]))
    scan_total = sum(r["扫描侧"] for r in v.values())
    L += ["", "本窗口扫描侧合计 **{}** 条。".format(scan_total)]
    if scan_total == 0:
        L.append("⚠️ **扫描侧为 0，本周无法比较任何 segment 的填补速度。**"
                 " 此时按 weekly_inbox 排名得到的任何结论都只反映 Apollo 上限，不是证据。")
    L.append("")

    L += ["**② 探测健康度**", "",
          "判据是探测记录库自身的行数，不是 scan_log 的 hits——"
          "后者是入水箱口径，而探测按设计不写水箱，它的 0 是结构性的。", "",
          "- 预期每日 {} 条".format(ph["expected_per_day"]),
          "- 探测记录库总行数：**{}**".format(ph["total_rows"]),
          "- 本窗口有数据的天数：{}".format(ph["days_with_data_in_window"]),
          "- 本窗口零记录的整天：{}".format(
              "、".join(ph["missing_days_in_window"]) or "无"),
          "- 判定：**{}**".format(ph["verdict"]), ""]

    L += ["**③ 信号命中率分母（修正 vs metrics.py 现口径）**", "",
          "- 扫描日志文件总数 {}，其中落在本窗口 {}".format(
              sd["files_total"], sd["files_in_window"]),
          "- 按窗口过滤：`{}`".format(json.dumps(sd["windowed"], ensure_ascii=False)),
          "- metrics.py 看到的（全历史累加）：`{}`".format(
              json.dumps(sd["cumulative_as_metrics_py_sees_it"], ensure_ascii=False)),
          "- 失真量（累计 − 窗口）：`{}`".format(
              json.dumps(sd["drift"], ensure_ascii=False)),
          "- {}".format(sd["note"]), "",
          "> 本脚本不改 metrics.py（③ 属 Phase 2 §八 明文保留的待拍板项）。",
          "> 根治只需在 metrics.py 遍历 scan 文件处加一个窗口过滤，那是真人的决定。", ""]
    return "\n".join(L)


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--completed-week", dest="completed_week", action="store_true",
                        help="改用 metrics.py 的窗口（上一个已完成周），便于与复盘包对齐。"
                             "默认看进行中的本周——健康检查要回答的是「现在怎么样」")
    args = parser.parse_args()
    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        scan_cfg = ac.load_config("scan.yaml")
        segments = list(ac.load_config("segments.yaml")["segments"].keys())

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        # 默认窗口是**进行中的本周**：上一个锚点 → 此刻。
        #
        # 2026-08-05 实测踩到：直接用 ac.week_window 拿到的是上一个**已完成**周
        # （周三跑，窗口是上周五到本周五之前的那一段），于是当天所有活动都在窗口外，
        # 全表为 0。那对每周五复盘是对的口径，对健康检查毫无用处——
        # 健康检查要回答的是「现在怎么样」，不是「上周怎么样」。
        completed = ac.week_window(th)
        if args.completed_week:
            window = completed
        else:
            window = {"start": completed["end"], "end": datetime.now(tz),
                      "tz": tz, "label": "{}（进行中）".format(completed["label"])}

        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        pipeline = notion.query_all(env["DS_PIPELINE"])
        probe_rows = notion.query_all(env["DS_PROBE"])

        probe_cfg = scan_cfg.get("probe") or {}
        expected = (len(probe_cfg.get("question_sets") or []) or 2) \
            * (probe_cfg.get("questions_per_set") or 5) \
            * (len(probe_cfg.get("engines") or []) or 3)

        result = {
            "script": SCRIPT, "mode": sc.resolve_mode(args),
            "generated_at": datetime.now(tz).isoformat(),
            "week_label": window["label"],
            "window_start": window["start"].isoformat(),
            "window_end": window["end"].isoformat(),
            "fill_velocity": fill_velocity(pipeline, window, tz, segments),
            "probe_health": probe_health(probe_rows, tz, window, expected),
            "scan_denominator": scan_denominator(window, tz),
        }
        body = render(result)
        result["report"] = body
        sc.emit(SCRIPT, result, th)
        print(body, file=sys.stderr)

        if sc.resolve_mode(args) == "commit":
            path = ac.write_outbox(
                "a1_health_{}.md".format(datetime.now(tz).strftime("%Y-%m-%d")), body)
            print("PUSH: {}".format(path))
        return 0
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
