#!/usr/bin/env python3
"""AEO Engine · J4 —— 为冷 outreach sequence 挑候选公司（只读，零 credit）。

依据：Build Spec · Phase 3 §J4 补充「Apollo sequence 按 segment 建；每段 200 到 400 家」。

与 apollo_poll.py 的分工——两者都查 Apollo，但目的完全不同，不要混用：
  apollo_poll.py  —— 往**水箱**灌人，每段每轮 10 行，供 J4 的 warm 私信一条条触达
  sequence_list.py —— 给**冷邮件 sequence** 挑 200-400 家公司的候选池，不进水箱

本脚本刻意**不做**三件事：
  1. 不富化（enrichment 按人计费）。搜索本身零 credit，2026-08-05 实测：1315 → 1315
  2. 不写 Notion
  3. 不碰 Apollo 任何写接口

所以它跑多少次都不花钱，可以放心用来反复校准 segment 条件——
而校准正是现在最需要的：A 段的 Apollo 条件只在 48 家公司的规模上验过一次
（Phase 2 §七 疑点②：改后的 keywords 仍是推演的，行业筛选是降级的），
拿它直接去富化 400 家等于用没验过的条件下一笔占余额三成到六成的赌注。

用法：
    python3 scripts/sequence_list.py --segment A --companies 400
    python3 scripts/sequence_list.py --segment A --companies 100 --pages 5
输出：logs/sequence_list_<date>.json + stdout 摘要
"""

import argparse
import json
import os
import sys
import time
from collections import OrderedDict

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "sequence_list"

# 代理商/供给侧的名称特征。命中不等于一定是代理商，只是**提请人工看一眼**。
# 出处：Phase 2 裁决④——A 段原条件捞上来的全是营销代理商（供给侧），
# 而 A 段要的是品牌方市场部（需求侧）。这是那次翻车的传感器。
AGENCY_MARKERS = [
    "agency", "agencies", "studio", "studios", "media group", "marketing group",
    "creative", "digital marketing", "advertising", "consulting", "consultancy",
    "productions", "production house", "collective", "partners",
]


def apollo(session, cfg, api_key, path, payload):
    resp = session.post(cfg["base_url"] + path, json=payload,
                        headers={cfg["auth_header"]: api_key,
                                 "Content-Type": "application/json",
                                 "accept": "application/json"},
                        timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError("Apollo HTTP {}：{}".format(resp.status_code, resp.text[:300]))
    return resp.json()


def credits(session, cfg, api_key):
    try:
        r = session.get(cfg["base_url"] + "/users/api_profile?include_credit_usage=true",
                        headers={cfg["auth_header"]: api_key, "accept": "application/json"},
                        timeout=30)
        return (r.json() or {}).get("num_credits_remaining")
    except Exception:  # noqa: BLE001
        return None


def pipeline_companies(env):
    """水箱里已有的公司名（归一化）。

    **这不是优化，是硬约束。** J4 的 warm 链每天从水箱出草稿由真人手工发 LinkedIn 私信；
    冷 sequence 一旦激活，Apollo 会自动给名单里的人发邮件。两条链互不知情——
    draft_runner 不看 sequence，apollo_sequence 不看水箱。
    同一家公司同时被两条链打，对方看到的是「这家公司在两个渠道同时轰我」，
    而那正是 warm 触达想避免的观感。

    2026-08-05 实测：A 段候选 400 家里有 5 家已在水箱（bolt / Circle / Doxis /
    Firmable / saas），而水箱 A 段一共只有 6 家公司——**83% 重合**。
    原因是两条链用同一份 segments.yaml 条件、同一个 Apollo 默认排名、都从 page 1 起。
    """
    notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
    rows = notion.query_all(env["DS_PIPELINE"])
    out = {}
    for r in rows:
        p = r.get("properties", {})
        name = ac.rich_text(p, "公司")
        key = " ".join((name or "").split()).lower()
        if key:
            out.setdefault(key, {"公司": name,
                                 "segment": ac.select_name(p, "segment"),
                                 "状态": ac.select_name(p, "状态")})
    return out


def looks_like_agency(name):
    low = (name or "").lower()
    return [m for m in AGENCY_MARKERS if m in low]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--segment", required=True, help="segment 字母，如 A")
    parser.add_argument("--companies", type=int, default=400, help="目标公司数上限")
    parser.add_argument("--pages", type=int, default=20, help="最多翻几页（每页 100 人）")
    parser.add_argument("--per-page", type=int, default=100, dest="per_page")
    parser.add_argument("--no-locations", dest="no_locations", action="store_true",
                        help="不加地域筛选（用来跟加了之后做对照，看噪音率是真降了还是采样波动）")
    args = parser.parse_args()

    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        segments = ac.load_config("segments.yaml")["segments"]
        cfg = ac.load_config("scan.yaml")["apollo"]

        outreach = ac.load_config("outreach.yaml")
        locations = ((outreach.get("sequence") or {}).get("targeting") or {}).get("person_locations") or []
        if args.no_locations:
            locations = []

        seg = segments.get(args.segment)
        if not seg:
            raise RuntimeError("segments.yaml 里没有 segment {}".format(args.segment))
        api_key = env.get("APOLLO_API_KEY")
        if not api_key:
            sc.missing_credential(SCRIPT, "APOLLO_API_KEY", "挑候选公司需要 Apollo API key", th)

        session = requests.Session()
        before = credits(session, cfg, api_key)

        tags = list(seg["apollo"].get("keywords") or []) + list(seg["apollo"].get("industries") or [])
        titles = (seg["titles"]["pain_feeler"] or []) + (seg["titles"]["decision_maker"] or [])
        pf = set(seg["titles"]["pain_feeler"] or [])
        ranges = [str(seg["apollo"].get("employee_count") or "").replace("-", ",")]

        companies = OrderedDict()
        total_entries, people_seen, pages_done = None, 0, 0

        for page in range(1, args.pages + 1):
            payload = {
                "person_titles": titles,
                "q_organization_keyword_tags": tags,
                "organization_num_employees_ranges": ranges,
                "page": page, "per_page": args.per_page,
            }
            if locations:
                payload["person_locations"] = locations
            body = apollo(session, cfg, api_key, cfg["people_search_path"], payload)
            if total_entries is None:
                total_entries = body.get("total_entries")
            people = body.get("people") or body.get("contacts") or []
            pages_done = page
            if not people:
                break
            for p in people:
                people_seen += 1
                org = p.get("organization") or {}
                name = (org.get("name") or "").strip()
                if not name:
                    continue
                slot = companies.setdefault(name, {
                    "公司": name,
                    "website": org.get("website_url"),
                    "employees": org.get("estimated_num_employees"),
                    "people": 0, "titles": [], "roles": set(),
                })
                slot["people"] += 1
                t = p.get("title") or ""
                if t and t not in slot["titles"]:
                    slot["titles"].append(t)
                slot["roles"].add("pain feeler" if t in pf else "decision maker")
            if len(companies) >= args.companies:
                break
            time.sleep(0.4)   # 温和翻页，别把额度打满

        after = credits(session, cfg, api_key)

        tank = pipeline_companies(env)

        rows, excluded = [], []
        for name, c in companies.items():
            if len(rows) >= args.companies:
                break
            key = " ".join(name.split()).lower()
            hit = tank.get(key)
            if hit:
                # 硬约束：水箱已有的公司一律不进 sequence 名单，没有开关。
                excluded.append({"公司": name, "why": "已在水箱",
                                 "水箱 segment": hit["segment"], "水箱状态": hit["状态"]})
                continue
            flags = looks_like_agency(name)
            rows.append({
                "公司": name,
                "website": c["website"],
                "employees": c["employees"],
                "命中人数": c["people"],
                "两侧角色齐": len(c["roles"]) >= 2,
                "titles": c["titles"][:4],
                "疑似代理商": flags,
            })

        suspects = [r for r in rows if r["疑似代理商"]]
        both = [r for r in rows if r["两侧角色齐"]]

        result = {
            "script": SCRIPT, "mode": "read-only",
            "segment": args.segment,
            "segment_name": seg.get("name"),
            "conditions": {"titles": titles, "keyword_tags": tags,
                           "employee_ranges": ranges,
                           "person_locations": locations or "（未加地域筛选）"},
            "apollo_total_entries": total_entries,
            "pages_fetched": pages_done, "people_seen": people_seen,
            "companies_found": len(companies),
            "companies_returned": len(rows),
            "excluded_already_in_pipeline": len(excluded),
            "excluded_detail": excluded,
            "pipeline_companies_total": len(tank),
            "both_sides_present": len(both),
            "suspected_agencies": len(suspects),
            "suspected_agency_rate": (round(len(suspects) / len(rows), 3) if rows else None),
            "credits_before": before, "credits_after": after,
            "credits_spent": (before - after) if (before is not None and after is not None) else None,
            "enriched": False,
            "wrote_notion": False,
            "companies": rows,
        }
        path = sc.emit(SCRIPT, result, th)

        print("", file=sys.stderr)
        print("segment {} —— {}".format(args.segment, seg.get("name")), file=sys.stderr)
        print("  Apollo 匹配总人数 : {}".format(total_entries), file=sys.stderr)
        print("  翻页 {} 页，看过 {} 人".format(pages_done, people_seen), file=sys.stderr)
        print("  去重后公司数      : {}".format(len(rows)), file=sys.stderr)
        print("  剔除·已在水箱     : {}（硬约束，无开关）".format(len(excluded)), file=sys.stderr)
        for e in excluded[:8]:
            print("      └ {}（水箱 {} / {}）".format(
                e["公司"], e["水箱 segment"], e["水箱状态"]), file=sys.stderr)
        print("  两侧角色齐的公司  : {}".format(len(both)), file=sys.stderr)
        print("  疑似代理商        : {}（{}）".format(
            len(suspects),
            "{:.1%}".format(len(suspects) / len(rows)) if rows else "-"), file=sys.stderr)
        print("  credit 消耗       : {} → {}".format(before, after), file=sys.stderr)
        print("  留档              : {}".format(path), file=sys.stderr)
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
