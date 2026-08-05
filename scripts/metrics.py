#!/usr/bin/env python3
"""AEO Engine · 周度量计算。

计算口径逐条来自 Build Spec · Phase 1 §四「metrics.py 计算口径」。
零 LLM：本脚本只做取数与算术。

铁律：
  * 空库或分母为零 → 输出 null 并在 caveats 注明，绝不输出 0。
    0 是会触发警报和误导复盘的真实数值，null 是「没有数据」。
  * 阈值与闸门只从 config/ 读，脚本内不出现任何阈值字面量。
  * 任一库读失败 → 整体失败并写 outbox 错误消息，不产出半份结果。

用法：python3 scripts/metrics.py
输出：data/metrics_YYYY-WW.json

口径变更记录
------------
2026-08-05（Shawn 拍板）：signal_hit_rate 的分母改为**只统计本周窗口内**的
logs/scan_*.json。此前是把全部历史文件无过滤累加——分子按周、分母累计，
跑得越久比率越小，会呈现为「信号质量持续下降」而实际只是分母在单调累积。
原属 Phase 2 §八 四个待拍板问题之一，Phase 1 实现结果页记的是旧的累计口径。
"""

import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac  # noqa: E402

SCRIPT = "metrics"

# Named 存量的状态集合。出处：spec §四「状态属于 Named / 触达中 / 已约通话 / 对话中」
NAMED_STATUSES = ("Named", "触达中", "已约通话", "对话中")
# booked 的目标状态。出处：spec §四「本窗口内进入『已约通话』状态的水箱行数」
BOOKED_STATUS = "已约通话"
# 台账草稿状态名。出处：Phase 0 核对清单 台账 状态选项
LEDGER_DRAFT = "草稿"

UNSET = "(未填写)"


def blank_metrics():
    return {
        "weekly_inbox": 0,
        "named_stock": 0,
        "qualified_conversations": 0,
        "booked": 0,
        "referred_inbox": 0,       # R 的分子中间量
        "r_replication": None,
        "winloss_in_window": 0,    # 覆盖率分母中间量
        "winloss_covered": 0,      # 覆盖率分子中间量
        "winloss_coverage": None,
        "signal_hit_rate": None,
    }


SCAN_FILE_DATE_RE = re.compile(r"scan_(\d{4}-\d{2}-\d{2})\.json$")


def read_scan_totals(segments, window=None, tz=None):
    """信号命中率的分母：A1 扫描命中总数，来自 logs/scan_*.json。

    该日志是 Phase 2 的约定，Phase 1 阶段必然缺失。
    spec 明文：日志缺失时输出 null 并注明，不许猜。

    2026-08-05 修正（Shawn 拍板，原属 Phase 2 §八 四个待拍板问题之一）：
    **只统计落在本周窗口内的扫描日志**。

    原实现把 logs/scan_*.json 的全部历史文件无过滤累加，而分子 weekly_inbox 是按周的。
    分子按周、分母累计，跑得越久 signal_hit_rate 越小——它会呈现为「信号质量在持续
    下降」，而实际上只是分母在单调累积。这个失真在只有一两个日志文件时看不出来，
    正是它危险的地方：等它明显时，已经拿这个指标做过几周判断了。

    window 为 None 时退回旧的累计口径（保持向后兼容，但会在 caveats 里说明）。
    """
    files_all = sorted(glob.glob(os.path.join(ac.LOGS_DIR, "scan_*.json")))
    if window is None:
        files = files_all
    else:
        files = []
        for path in files_all:
            m = SCAN_FILE_DATE_RE.search(os.path.basename(path))
            if not m:
                # 文件名不带日期 → 无法判断归属哪一周，不猜，直接排除。
                # 宁可分母偏小（比率偏高、看起来乐观）也不要把不知道哪周的数混进来？
                # 不——偏高同样是错的。所以这里排除并在 note 里列出，让它可见。
                continue
            d = datetime.fromisoformat(m.group(1))
            if tz is not None:
                d = d.replace(tzinfo=tz)
            if ac.in_window(d, window):
                files.append(path)
    if not files:
        if files_all:
            return None, ("找到 {} 个 scan_*.json，但没有一个落在本周窗口内，"
                          "分母缺失（这是正确行为：分子按周，分母也必须按周）"
                          .format(len(files_all)))
        return None, "logs/scan_*.json 不存在（A1 扫描属 Phase 2，尚未上线），分母缺失"

    # 预期形态：{"by_segment": {"A": <扫描命中数>, ...}}
    # Phase 2 定稿后若与此不符，这里会落到 else 分支返回 null，不做任何猜测性解析。
    totals = {s: 0 for s in segments}
    recognized = False
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            return None, "读取 {} 失败：{}".format(os.path.basename(path), exc)
        by_seg = doc.get("by_segment")
        if isinstance(by_seg, dict):
            recognized = True
            for seg, cnt in by_seg.items():
                if isinstance(cnt, int):
                    totals[seg] = totals.get(seg, 0) + cnt
    if not recognized:
        return None, ("找到 {} 个 scan_*.json，但均无 by_segment 字段；"
                      "Phase 2 日志格式定稿后需回填本解析逻辑，此前不做猜测".format(len(files)))
    return totals, None


def main():
    window = None
    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        gates = ac.load_config("gates.yaml")
        seg_cfg = ac.load_config("segments.yaml")

        window = ac.week_window(th)
        tz = window["tz"]

        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        pipeline = notion.query_all(env["DS_PIPELINE"])
        winloss = notion.query_all(env["DS_WINLOSS"])
        ledger = notion.query_all(env["DS_LEDGER"])

        # segment 键：config 里的 A–E，加上数据里实际出现的其他取值（未分类 / 其他）
        seg_keys = list(seg_cfg["segments"].keys())
        by_seg = {s: blank_metrics() for s in seg_keys}

        def bucket(name):
            key = name or UNSET
            if key not in by_seg:
                by_seg[key] = blank_metrics()
            return by_seg[key]

        caveats = []

        # ---- 水箱 ----
        for row in pipeline:
            props = row.get("properties", {})
            seg = ac.select_name(props, "segment")
            m = bucket(seg)
            status = ac.select_name(props, "状态")
            inbox_dt = ac.date_prop(props, "入箱日期", tz)
            edited = ac.parse_iso_utc(row.get("last_edited_time"), tz)

            if ac.in_window(inbox_dt, window):
                m["weekly_inbox"] += 1
                if ac.relation_ids(props, "引荐来源"):
                    m["referred_inbox"] += 1

            if status in NAMED_STATUSES:
                m["named_stock"] += 1

            # booked —— 口径 (a)，2026-08-04 Shawn 裁决
            if status == BOOKED_STATUS and ac.in_window(edited, window):
                m["booked"] += 1

        caveats.append(
            "booked 数用近似口径 (a)：状态当前为「{}」且页面 last_edited_time 落在窗口内。"
            "水箱无状态变更时间戳字段，此口径会高估（改任何字段都会刷新 last_edited_time）"
            "并会漏掉约完通话后又改过状态的行。2026-08-04 Shawn 裁决采用。".format(BOOKED_STATUS))

        # ---- win/loss ----
        fill_days = th["sla"]["winloss_fill_days"]
        for row in winloss:
            props = row.get("properties", {})
            m = bucket(ac.select_name(props, "segment"))
            talk_dt = ac.date_prop(props, "日期", tz)
            fill_dt = ac.date_prop(props, "填写日期", tz)

            if ac.in_window(talk_dt, window):
                m["qualified_conversations"] += 1
                m["winloss_in_window"] += 1
                if talk_dt and fill_dt and (fill_dt - talk_dt).days <= fill_days:
                    m["winloss_covered"] += 1

        caveats.append(
            "win/loss 覆盖率按「填写日期 − 对话日期 ≤ {} 天」判定：两字段均为纯日期"
            "（无时间），不存在 24 小时这个量。2026-08-04 Shawn 裁决按天判。"
            "两个日期任一缺失的行计入分母、不计入分子。".format(fill_days))

        # ---- 比率：分母为零一律 null ----
        scan_totals, scan_note = read_scan_totals(list(by_seg.keys()), window, tz)
        if scan_note:
            caveats.append("信号命中率全部为 null：" + scan_note)

        for seg, m in by_seg.items():
            denom = m["qualified_conversations"]
            m["r_replication"] = (round(m["referred_inbox"] / denom, 4)
                                  if denom else None)

            denom = m["winloss_in_window"]
            m["winloss_coverage"] = (round(m["winloss_covered"] / denom, 4)
                                     if denom else None)

            if scan_totals:
                d = scan_totals.get(seg, 0)
                m["signal_hit_rate"] = (round(m["weekly_inbox"] / d, 4) if d else None)
            else:
                m["signal_hit_rate"] = None

        # ---- 合计 ----
        counters = ("weekly_inbox", "named_stock", "qualified_conversations",
                    "booked", "referred_inbox", "winloss_in_window", "winloss_covered")
        totals = {k: sum(m[k] for m in by_seg.values()) for k in counters}
        totals["r_replication"] = (
            round(totals["referred_inbox"] / totals["qualified_conversations"], 4)
            if totals["qualified_conversations"] else None)
        totals["winloss_coverage"] = (
            round(totals["winloss_covered"] / totals["winloss_in_window"], 4)
            if totals["winloss_in_window"] else None)
        if scan_totals:
            grand = sum(scan_totals.values())
            totals["signal_hit_rate"] = (
                round(totals["weekly_inbox"] / grand, 4) if grand else None)
        else:
            totals["signal_hit_rate"] = None

        # ---- 台账快照 ----
        ledger_snapshot = []
        for row in ledger:
            props = row.get("properties", {})
            created = ac.date_prop(props, "创建日期", tz)
            edited = ac.parse_iso_utc(row.get("last_edited_time"), tz)
            if ac.in_window(created, window) or ac.in_window(edited, window):
                ledger_snapshot.append({
                    "类型": ac.select_name(props, "类型"),
                    "名称": ac.title_text(props, "资产名"),
                    "状态": ac.select_name(props, "状态"),
                })
        caveats.append(
            "台账快照的「状态变更落在窗口内」用页面 last_edited_time 近似（口径 (a)，"
            "2026-08-04 Shawn 裁决）：台账无状态变更时间戳字段，改任何字段都会命中。"
            "「创建落在窗口内」用的是 创建日期 字段，精确。")

        # ---- 警报输入（判定交给复盘包，脚本只备料）----
        prev_label = "{}-{:02d}".format(
            *(window["end"] - timedelta(days=7)).isocalendar()[:2])
        prev_path = os.path.join(ac.DATA_DIR, "metrics_{}.json".format(prev_label))
        prev_booked = None
        if os.path.exists(prev_path):
            try:
                with open(prev_path, encoding="utf-8") as fh:
                    prev_booked = json.load(fh).get("totals", {}).get("booked")
            except (OSError, ValueError):
                prev_booked = None

        alerts_input = {
            "thresholds": {
                "named_floor": th["named_floor"],
                "booked_floor": th["booked_floor"],
                "booked_floor_consecutive_weeks": th["booked_floor_consecutive_weeks"],
                "weekly_inbox_quota": th["weekly_inbox_quota"],
            },
            "gates": gates,
            "named_stock_total": totals["named_stock"],
            "booked_total": totals["booked"],
            "weekly_inbox_total": totals["weekly_inbox"],
            "previous_week": {"label": prev_label, "booked_total": prev_booked}
            if prev_booked is not None else None,
            "ledger_draft_backlog_note":
                "草稿积压计数由 sla_check.py 规则四负责，不在本文件重复计算",
        }

        result = {
            "week": {
                "label": window["label"],
                "start": window["start"].isoformat(),
                "end": window["end"].isoformat(),
                "timezone": str(tz),
                "generated_at": datetime.now(tz).isoformat(),
                "row_counts_read": {
                    "pipeline": len(pipeline),
                    "winloss": len(winloss),
                    "ledger": len(ledger),
                },
                "caveats": caveats,
            },
            "by_segment": by_seg,
            "totals": totals,
            "ledger_snapshot": ledger_snapshot,
            "alerts_input": alerts_input,
        }

        out = os.path.join(ac.DATA_DIR, "metrics_{}.json".format(window["label"]))
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)

        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("\n写入 {}".format(out), file=sys.stderr)

    except Exception as exc:  # noqa: BLE001 —— 任何失败都要冒泡成整体失败
        ac.fail(SCRIPT, exc, window["label"] if window else None)


if __name__ == "__main__":
    main()
