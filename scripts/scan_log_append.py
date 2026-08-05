#!/usr/bin/env python3
"""AEO Engine · A1 —— 把一轮扫描的日志记录并进 logs/scan_YYYY-MM-DD.json。

这是四份 playbook 共用的收尾工具，也是 Phase 1「信号命中率」分母的唯一入口。

为什么需要它（不是可有可无的第四个脚本）：
  Build Spec Phase 2 §四 定的记录结构是 {date, playbook, segment, hits, inboxed, skipped_dupe}，
  但 Phase 1 的 scripts/metrics.py:64 已经写死了它只认 {"by_segment": {"A": <数>, ...}}，
  认不出就把信号命中率整个判成 null。两边对不上，扫描日志就白写。
  硬约束是不动 Phase 1，所以让日志文件同时满足两边：
    records   —— 逐条按 spec 结构，一个字段不增不减
    by_segment —— records 里 hits 的按 segment 汇总，专供 metrics.py 取数
  由本脚本保证两者始终一致，不靠人手对。

用法：
    echo '{"date":"2026-08-10","playbook":"scan_linkedin_weekly",
           "records":[{"segment":"A","hits":12,"inboxed":8,"skipped_dupe":4}]}' \\
      | python3 scripts/scan_log_append.py

    python3 scripts/scan_log_append.py --file /tmp/run.json
    python3 scripts/scan_log_append.py --show 2026-08-10     # 只看不写

同一个 (date, playbook, segment) 重复并入时按「后到覆盖」处理——同一轮扫描重跑
应该修正而不是叠加计数。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "scan_log_append"
RECORD_FIELDS = ("date", "playbook", "segment", "hits", "inboxed", "skipped_dupe")


def log_path(date):
    return os.path.join(ac.LOGS_DIR, "scan_{}.json".format(date))


def normalize(doc):
    """把输入统一成 (date, [完整记录, ...])。缺字段就报错，不补默认值。"""
    if isinstance(doc, list):
        records, date, playbook = doc, None, None
    else:
        records = doc.get("records") or ([doc] if "segment" in doc else [])
        date, playbook = doc.get("date"), doc.get("playbook")

    if not records:
        raise ValueError("输入里没有任何记录：需要 records 数组，或单条含 segment 的记录")

    out = []
    for rec in records:
        merged = {"date": rec.get("date") or date, "playbook": rec.get("playbook") or playbook}
        merged.update({k: rec.get(k) for k in ("segment", "hits", "inboxed", "skipped_dupe")})
        missing = [k for k in RECORD_FIELDS if merged.get(k) is None]
        if missing:
            raise ValueError("记录缺字段 {}：{}".format(missing, json.dumps(rec, ensure_ascii=False)))
        for k in ("hits", "inboxed", "skipped_dupe"):
            if not isinstance(merged[k], int):
                raise ValueError("{} 必须是整数，收到 {!r}".format(k, merged[k]))
        out.append({k: merged[k] for k in RECORD_FIELDS})

    dates = {r["date"] for r in out}
    if len(dates) != 1:
        raise ValueError("一次只并入同一天的记录，收到多个日期：{}".format(sorted(dates)))
    return out[0]["date"], out


def merge(date, new_records, scan_cfg):
    path = log_path(date)
    existing = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = (json.load(fh) or {}).get("records") or []

    by_key = {(r.get("playbook"), r.get("segment")): r for r in existing}
    for r in new_records:
        by_key[(r["playbook"], r["segment"])] = r
    records = sorted(by_key.values(), key=lambda r: (r["playbook"], r["segment"]))

    field = scan_cfg["scan_log"]["by_segment_source_field"]
    by_segment = {}
    for r in records:
        by_segment[r["segment"]] = by_segment.get(r["segment"], 0) + r[field]

    return path, {"date": date, "records": records, "by_segment": by_segment,
                  "by_segment_source_field": field}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="从文件读 JSON，默认从 stdin 读")
    parser.add_argument("--show", metavar="DATE", help="只打印该日期的日志，不写入")
    args = parser.parse_args()

    th = ac.load_config("thresholds.yaml")
    try:
        scan_cfg = ac.load_config("scan.yaml")

        if args.show:
            path = log_path(args.show)
            if not os.path.exists(path):
                raise ValueError("不存在：{}".format(os.path.relpath(path, ac.REPO)))
            with open(path, encoding="utf-8") as fh:
                print(json.dumps(json.load(fh), ensure_ascii=False, indent=2))
            return sc.EXIT_OK

        raw = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
        if not raw.strip():
            raise ValueError("没有输入。用 --file 指定文件，或把 JSON 从管道喂进来。")

        date, records = normalize(json.loads(raw))
        path, doc = merge(date, records, scan_cfg)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

        print(json.dumps({
            "script": SCRIPT, "status": "ok",
            "file": os.path.relpath(path, ac.REPO),
            "merged_records": len(records), "total_records": len(doc["records"]),
            "by_segment": doc["by_segment"],
            "note": "by_segment 供 Phase 1 的 metrics.py 取数（信号命中率分母）；"
                    "records 按 Phase 2 spec §四 的字段结构逐条留存。",
        }, ensure_ascii=False, indent=2))
        return sc.EXIT_OK

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
