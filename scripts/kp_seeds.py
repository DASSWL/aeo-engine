#!/usr/bin/env python3
"""AEO Engine · A1 —— 导出该拿去 Keyword Planner 查搜索量的词。

Shawn 2026-08-05 指定新增的两条进料路径之二（路径 2：AI 建议与 Reddit 的词跑 KP 验量）
在缺 Google Ads 凭据时**唯一能做且有用的那一半**。

它回答一个问题：**现在 Query 库里哪些词还没有任何市场证据。**
判据：`月搜索量` 为空 **且** `搜索量区间` 也为空。
两列都空 = 没有任何人验证过有人真的搜它。
（`搜索量区间` 是 Phase 0 字段表 2026-08-05 解冻加的第 8 列。有区间就是有量级证据，
  精度未知而已，不该再拉去重验一遍。）

为什么需要单独一个脚本，而不是用 keyword_volume.py --print-seeds：
  那个打印的是 **config 里的 28 条种子**（spec 首批 3 条 + segments.yaml 25 条），
  是我们自己写的词。本脚本打印的是 **Query 库里缺量的行**，
  其中包含 AI 追问带回来的、以及将来 Reddit 带回来的——那些才是我们没想到的词。
  两者是不同的集合，混用会让「验的是哪批词」说不清楚。

拿到清单之后的闭环（当前是真人手动的那一段）：
  1. 本脚本 --paste 打印词表 → 贴进 Google Keyword Planner
  2. 导出 CSV 放 data/kw/
  3. 跑 keyword_volume.py --commit —— 它会认出这些行已存在，走 to_update 补上月搜索量

✅ 第 3 步曾有一个会造成静默数据损失的问题，2026-08-05 经 Shawn 拍板已修：
   `keyword_volume.py` 的 update 分支原本同时写 `月搜索量` **和** `数据来源`，
   把后者覆写成「Keyword Planner」——AI 追问那批刚标上的「AI 建议」、
   Reddit 那批的「A1 扫描」，会在补量的一瞬间被抹掉，
   且是**静默**抹掉：补量成功了，数字是对的，出处没了，没有任何报警会响。
   现已改成只在 `数据来源` 为空时才写（与 serp_scan.py:184 同一条口径），
   wire 级实测确认来源非空的行只下发 `月搜索量`。
   本脚本仍逐次统计涉及行数（输出的 `overwrite_risk`），当作回归哨兵。

只读、零成本、不写 Notion、不调任何计费 API。

用法：
    python3 scripts/kp_seeds.py            # 报告：缺量的词、按来源分布、覆写风险
    python3 scripts/kp_seeds.py --paste    # 只打印词表本身，一行一个，供直接贴进 KP
"""

import argparse
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "kp_seeds"

# 这些来源的行一旦被 keyword_volume 的 update 分支碰到，`数据来源` 就会被覆写掉。
# 「Keyword Planner」本来就是它自己写的，覆写无损失；其余四个都有损失。
OVERWRITE_SAFE = ("Keyword Planner",)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paste", action="store_true",
                        help="只打印词表，一行一个，不打印任何其他内容")
    args = parser.parse_args()

    th = ac.load_config("thresholds.yaml")
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])

        env = ac.load_env()
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        rows = notion.query_all(env["DS_QUERY"])

        missing, have = [], []
        for r in rows:
            p = r.get("properties", {})
            text = ac.title_text(p, "query 文本")
            if not text:
                continue
            vp = p.get("月搜索量") or {}
            vol = vp.get("number") if vp.get("type") == "number" else None
            rng = ac.rich_text(p, "搜索量区间").strip()
            item = {"query 文本": text,
                    "搜索量区间": rng,
                    "数据来源": ac.select_name(p, "数据来源"),
                    "状态": ac.select_name(p, "状态"),
                    "类型": ac.select_name(p, "类型"),
                    "月搜索量": vol, "page_id": r["id"]}
            (have if (vol is not None or rng) else missing).append(item)

        if args.paste:
            for m in missing:
                print(m["query 文本"])
            return sc.EXIT_OK

        by_source = Counter(m["数据来源"] or "(空)" for m in missing)
        at_risk = [m for m in missing if m["数据来源"] not in OVERWRITE_SAFE
                   and m["数据来源"]]

        kw_dir = os.path.join(ac.REPO, "data", "kw")
        csv_count = len([f for f in os.listdir(kw_dir)
                         if f.lower().endswith((".csv", ".tsv"))]) \
            if os.path.isdir(kw_dir) else 0

        google_ads_keys = ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID",
                           "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN",
                           "GOOGLE_ADS_CUSTOMER_ID")
        missing_creds = [k for k in google_ads_keys if not env.get(k)]

        result = {
            "script": SCRIPT, "status": "ok", "wrote_notion": False,
            "generated_at": datetime.now(tz).isoformat(),
            "counts": {
                "query_db_total": len(rows),
                "无任何量证据（月搜索量与搜索量区间皆空）": len(missing),
                "已有精确值或区间": len(have),
            },
            "缺量行按数据来源": dict(by_source),
            "seeds": [m["query 文本"] for m in missing],
            "rows": missing,
            "csv_files_in_data_kw": csv_count,
            "google_ads_missing_credentials": missing_creds,
            "overwrite_risk": {
                "行数": len(at_risk),
                "涉及来源": dict(Counter(m["数据来源"] for m in at_risk)),
                "问题（已修）": "keyword_volume.py 的 update 分支曾会把 `数据来源` "
                        "覆写成「Keyword Planner」，抹掉这些行现有的来源标注",
                "后果": "2026-08-05 解冻 Phase 0 加的「AI 建议」「A1 扫描」两个取值失效，"
                        "事后再也分不清哪条词是模型给的、哪条是真人发帖里抓的",
                "改法": "照 serp_scan.py:184 的做法，只在 `数据来源` 为空时才写",
                "状态": "✅ 2026-08-05 已修（Shawn 拍板）。本项保留为回归哨兵："
                        "若哪天这些行的 `数据来源` 又变成「Keyword Planner」，"
                        "说明那处改动被回退了",
            },
            "next_steps": [
                "python3 scripts/kp_seeds.py --paste  → 贴进 Google Keyword Planner",
                "导出 CSV 放 data/kw/",
                "python3 scripts/keyword_volume.py        （先 dry-run 看一遍）",
                "确认 overwrite_risk 已处置后，再 --commit",
            ],
        }
        sc.emit(SCRIPT, result, th)

        L = ["🌱 AEO · 待验量的 query（Keyword Planner 种子）",
             "",
             "Query 库 {} 行，其中 **无任何量证据 {} 行**、已有精确值或区间 {} 行。".format(
                 len(rows), len(missing), len(have)),
             "缺量行按来源：`{}`".format(dict(by_source)),
             "`data/kw/` 现有 CSV/TSV：{} 个".format(csv_count),
             "Google Ads 凭据：缺 {} 项（API 路径不可用，只能走真人导 CSV）".format(
                 len(missing_creds)),
             ""]
        if at_risk:
            L += ["🛡 **{} 行带着非 KP 的来源标注**（{}）——补量后这些标注应当原样保留。".format(
                      len(at_risk), dict(Counter(m["数据来源"] for m in at_risk))),
                  "覆写问题已于 2026-08-05 修掉（`keyword_volume` 只在 `数据来源` 为空时才写）。",
                  "**这一行是回归哨兵**：下次补完量再跑本脚本，若这些行变成了",
                  "「Keyword Planner」，说明那处改动被回退了。", ""]
        print("\n".join(L), file=sys.stderr)
        return sc.EXIT_OK

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
