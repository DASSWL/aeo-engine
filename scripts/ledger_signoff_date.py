#!/usr/bin/env python3
"""AEO Engine · 台账「签发日期」自动回填。

存在的理由（Shawn 2026-08-12 拍板「签发我只改签发状态，签发日期自动回填」）：

  签发原本是**两件事**：把「状态」改成「已签发」+ 填「签发日期」。
  漏填第二件的代价被站点 lint 两头堵死——填了 frontmatter 的 signed_off
  报「台账签发日期是空的」，不填报「signed_off 为空」，两条路都 build fail
  （2026-08-10 那次四行全踩中，README「发布链路补全」一节有原委）。

  真人手动做两件事而其中一件不做就整条链断，这个设计本身是错的。
  本脚本把第二件自动化：只要状态是「已签发」而日期是空的，就补上。

**日期取的是 Notion 的 last_edited_time，不是脚本跑的那一天。**
理由：真人翻状态那一下就是最后一次编辑，last_edited_time 因此是签发时刻
最接近的可得值；用「脚本发现的那天」会把日期系统性地往后推。

⚠️ 已知不精确：翻完状态之后如果那一行又被编辑过（改资产名、贴发布链接等），
last_edited_time 就跟着走了，回填的日期会晚于真实签发日。
所以本脚本**每天跑**，把这个窗口压到 24 小时内；且只在日期为空时写一次，
写过就不再碰——已经填了的日期是真人的，脚本不许覆盖。

写入前后各独立回读一次，写完逐行核对。

用法：
    python3 scripts/ledger_signoff_date.py             # dry-run，只报会填什么
    python3 scripts/ledger_signoff_date.py --commit    # 真回填
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "ledger_signoff_date"
SIGNED = "已签发"
# 「已发布」「已下线」是签发之后的状态：它们同样代表这一行已经被签发过。
# 只认「已签发」会漏掉那些签发后很快就推到已发布的行——那些行的日期一样是空的，
# 一样会让 lint build fail。
SIGNED_STATES = (SIGNED, "已发布", "已下线")


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    args = parser.parse_args()
    mode = sc.resolve_mode(args)

    th = ac.load_config("thresholds.yaml")
    try:
        env = ac.load_env()
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        rows = notion.query_all(env["DS_LEDGER"])

        to_fill, already, not_signed = [], [], []
        for r in rows:
            p = r.get("properties", {})
            status = ac.select_name(p, "状态")
            facing = ac.rich_text(p, "面向")
            signed = ((p.get("签发日期") or {}).get("date") or {}).get("start")
            if status not in SIGNED_STATES:
                not_signed.append({"面向": facing, "状态": status})
                continue
            if signed:
                already.append({"面向": facing, "状态": status, "签发日期": signed[:10]})
                continue
            edited = (r.get("last_edited_time") or "")[:10]
            to_fill.append({"row_id": r["id"], "面向": facing, "状态": status,
                            "签发日期": edited,
                            "date_source": "Notion last_edited_time"})

        written = []
        if mode == "commit":
            for item in to_fill:
                if not item["签发日期"]:
                    # 取不到 last_edited_time 就不猜。宁可留空让 lint 继续挡，
                    # 也不要往台账里塞一个编出来的签发日。
                    continue
                notion.update_page(item["row_id"],
                                   {"签发日期": sc.p_date(item["签发日期"])})
                written.append({"面向": item["面向"], "签发日期": item["签发日期"]})

            # 独立回读：重新拉库核对，不信 update 回执
            fresh = {r["id"]: r for r in notion.query_all(env["DS_LEDGER"])}
            for item in to_fill:
                row = fresh.get(item["row_id"])
                got = (((row or {}).get("properties", {}).get("签发日期") or {})
                       .get("date") or {}).get("start")
                item["readback_ok"] = bool(got) and got[:10] == item["签发日期"]

        sc.emit(SCRIPT, {
            "script": SCRIPT, "mode": mode, "status": "ok",
            "wrote_notion": mode == "commit",
            "counts": {"ledger_rows": len(rows), "to_fill": len(to_fill),
                       "already_dated": len(already),
                       "not_signed_off": len(not_signed)},
            "to_fill": to_fill, "written": written,
            "already_dated": already,
            "notes": [
                "日期取 Notion last_edited_time，不是脚本跑的那一天——"
                "真人翻状态那一下就是最后一次编辑。",
                "只在「签发日期」为空时写一次；已填的是真人的，脚本不覆盖。",
                "认「已签发/已发布/已下线」三种状态：后两种同样代表签发发生过。",
            ],
        }, th)
        return 0

    except SystemExit:
        raise
    except Exception as exc:                                   # noqa: BLE001
        ac.fail(SCRIPT, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
