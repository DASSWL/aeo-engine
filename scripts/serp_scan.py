#!/usr/bin/env python3
"""AEO Engine · A1 —— 目标 query 的搜索结果页记录（谁在占位、评测站页）。

依据：Build Spec · Phase 2 §一.2 与 §四「脚本要点 · serp_scan.py」。

schema 缺口已解决（2026-08-06，Phase 0 字段表第四次解冻，Shawn 拍板）：
   Query 库 additive 加了第 9 列 `SERP 占位`（rich_text）。--commit 时写入
   「top3 占位域名 + 评测站页 + 扫描日期」的紧凑摘要，完整占位者名单仍以
   logs/serp_scan_*.json 为准（rich_text 装不下全量，列里只放看一眼要用的）。
   `数据来源` 的行为不变：仅在为空时写「SERP 观察」，不覆盖已有来源标注。

用法：
    python3 scripts/serp_scan.py                         # dry-run，取 Query 库按量前 N
    python3 scripts/serp_scan.py --queries "a" "b" "c"    # 指定 query（首批三个用这个）
    python3 scripts/serp_scan.py --commit
"""

import glob
import json
import os
import sys
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "serp_scan"


def month_usage(prefix):
    """本月已用的 SerpAPI 调用次数，从既往运行日志累加。

    免费层每月 100 次，用超了要么被拒要么开始计费——没有本地计数器就等于没有闸。
    """
    used, files = 0, []
    for path in sorted(glob.glob(os.path.join(ac.LOGS_DIR, "{}_*.json".format(SCRIPT)))):
        base = os.path.basename(path)
        if "_{}".format(prefix) not in base:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        n = doc.get("api_calls")
        if isinstance(n, int):
            used += n
            files.append({"file": base, "api_calls": n})
    return used, files


def pick_queries(rows, top_n, sort_floor):
    """Query 库按「量」降序取前 N。没有任何量证据的排在最后（未知不等于 0）。

    「量」有两种来源，2026-08-05 Shawn 拍板后两种都认：
      * `月搜索量`  —— 精确值，直接用
      * `搜索量区间` —— Phase 0 字段表当天解冻加的第 8 列。取 `sort_floor`
                       给的**下界**参与排序，不取中值。

    为什么取下界：下界是「至少这么多」，是这条区间能保证的事实。
    拿中值（1K–10K → 5000）去压一个精确值 200，是拿推断压过测量；
    拿下界（1000）比，仍然赢，但赢在一个不会错的数上。

    这个数**只用于排序，永不写库**——写进 Query 库的仍然只有区间字符串本身。

    改这一条之前，本函数只看 `月搜索量`，于是桶化来的行（月搜索量全空）
    在 SERP 选词里永远排最后，明明有量级信息只是不在那一列。
    """
    items = []
    for r in rows:
        props = r.get("properties", {})
        text = ac.title_text(props, "query 文本")
        if not text:
            continue
        vol_prop = props.get("月搜索量") or {}
        vol = vol_prop.get("number") if vol_prop.get("type") == "number" else None
        rng = ac.rich_text(props, "搜索量区间").strip() or None
        ds_prop = props.get("数据来源") or {}
        ds = (ds_prop.get("select") or {}).get("name") if ds_prop.get("type") == "select" else None

        if vol is not None:
            mag, basis = vol, "月搜索量（精确值）"
        elif rng and rng in sort_floor:
            mag, basis = sort_floor[rng], "搜索量区间下界（{}）".format(rng)
        else:
            mag, basis = None, ("区间 {!r} 不在 sort_floor 表里".format(rng)
                                if rng else "无任何量证据")
        items.append({"query 文本": text, "月搜索量": vol, "搜索量区间": rng,
                      "排序量级": mag, "排序依据": basis,
                      "数据来源": ds, "page_id": r["id"]})
    items.sort(key=lambda x: (x["排序量级"] is None, -(x["排序量级"] or 0)))
    return items[:top_n], len(items)


def domain_of(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def call_serpapi(session, endpoint, params, query, api_key):
    payload = dict(params)
    payload.update({"q": query, "api_key": api_key})
    resp = session.get(endpoint, params=payload, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError("SerpAPI HTTP {}：{}".format(resp.status_code, resp.text[:300]))
    return resp.json()


def extract(body, review_domains):
    """从 SerpAPI 响应里取占位者与评测站页。只取实际存在的字段，缺就是缺。"""
    occupants, review_pages = [], []
    for item in body.get("organic_results") or []:
        link = item.get("link")
        dom = domain_of(link)
        rec = {"position": item.get("position"), "title": item.get("title"),
               "link": link, "domain": dom}
        occupants.append(rec)
        if any(dom == d or dom.endswith("." + d) for d in review_domains):
            review_pages.append(rec)
    return occupants, review_pages


def serp_cell(occupants, review_pages, date_str, max_len=1900):
    """`SERP 占位` 列的紧凑摘要。rich_text 上限 2000 字符，超长截断留标记。

    只放看一眼要用的：前 3 占位域名、评测站页 URL、扫描日期。
    完整名单在 logs/serp_scan_*.json，这列不是它的替代品。
    """
    top3 = [o["domain"] for o in occupants[:3] if o.get("domain")]
    parts = ["top3: {}".format(", ".join(top3) if top3 else "(无结果)")]
    if review_pages:
        parts.append("评测站: {}".format(
            " ; ".join(p["link"] for p in review_pages[:2] if p.get("link"))))
    parts.append("扫描 {}".format(date_str))
    text = " | ".join(parts)
    if len(text) > max_len:
        text = text[:max_len] + "…(截断)"
    return text


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--queries", nargs="+", default=None,
                        help="直接指定要扫的 query，跳过 Query 库排序取词")
    parser.add_argument("--top-n", type=int, default=None, dest="top_n",
                        help="覆盖 scan.yaml 的 top_n_by_volume（试跑省额度用）")
    args = parser.parse_args()
    mode = sc.resolve_mode(args)

    th = ac.load_config("thresholds.yaml")
    try:
        scan_cfg = ac.load_config("scan.yaml")
        serp_cfg = scan_cfg["serp"]
        env = ac.load_env()

        # 选词：显式指定优先，否则读 Query 库按量取前 N
        #（量 = 月搜索量精确值，或 搜索量区间 的下界。见 pick_queries）
        if args.queries:
            targets = [{"query 文本": q, "月搜索量": None, "搜索量区间": None,
                        "排序量级": None, "排序依据": "--queries 显式指定",
                        "数据来源": None,
                        "page_id": None, "selected_by": "--queries 显式指定"}
                       for q in args.queries]
            query_db_total = None
        else:
            notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
            rows = notion.query_all(env["DS_QUERY"])
            bucket_cfg = ac.load_config("kp_buckets.yaml")
            top_n = args.top_n or serp_cfg["top_n_by_volume"]
            targets, query_db_total = pick_queries(
                rows, top_n, bucket_cfg["sort_floor"])
            for t in targets:
                t["selected_by"] = "Query 库按量前 {}（月搜索量精确值或搜索量区间下界）".format(
                    top_n)

        now = sc.now_local(th)
        used, usage_files = month_usage(now.strftime("%Y-%m"))
        remaining = serp_cfg["monthly_quota"] - used

        plan = {
            "endpoint": serp_cfg["endpoint"],
            "provider": serp_cfg["provider"],
            "params": serp_cfg["params"],
            "targets": targets,
            "monthly_quota": serp_cfg["monthly_quota"],
            "quota_used_this_month": used,
            "quota_remaining": remaining,
            "quota_source_files": usage_files,
        }

        api_key = env.get("SERPAPI_KEY") or env.get("SERPAPI_API_KEY")
        if not api_key:
            sc.missing_credential(
                SCRIPT, "SERPAPI_KEY",
                "SerpAPI key 不在 .env。spec §四 把 SerpAPI 列为可选凭据，"
                "所以这不是阻塞项，但没有 key 就一条 SERP 都取不到。"
                "下面的 plan 是已解析完成的执行计划（选词、参数、配额），"
                "**不含任何搜索结果**——没有的东西不编。", th,
                extra={"mode": mode, "plan": plan, "query_db_total": query_db_total})

        if remaining <= 0:
            raise RuntimeError(
                "本月 SerpAPI 配额已用尽（配额 {}，已用 {}）。不越额调用。".format(
                    serp_cfg["monthly_quota"], used))
        if len(targets) > remaining:
            targets = targets[:remaining]
            plan["truncated_to_remaining"] = len(targets)

        session = requests.Session()
        date_str = now.strftime("%Y-%m-%d")
        results, calls = [], 0
        for t in targets:
            body = call_serpapi(session, serp_cfg["endpoint"], serp_cfg["params"],
                                t["query 文本"], api_key)
            calls += 1
            occupants, review_pages = extract(body, serp_cfg["review_site_domains"])
            t["SERP 占位"] = serp_cell(occupants, review_pages, date_str)
            results.append({
                "query 文本": t["query 文本"],
                "月搜索量": t["月搜索量"],
                "搜索量区间": t.get("搜索量区间"),
                "排序依据": t.get("排序依据"),
                "占位者": occupants,
                "评测站页": review_pages,
                "占位者数": len(occupants),
                "评测站页数": len(review_pages),
                "SERP 占位": t["SERP 占位"],
            })

        # 写库（2026-08-06 起有两处可落）：
        #   `SERP 占位` —— 每次扫描覆盖写（它是「最近一次扫描的快照」，历史在 logs）
        #   `数据来源` —— 仅在为空时写「SERP 观察」，不覆盖已有来源标注
        ds_value = scan_cfg["query"]["data_source_values"]["serp_scan"]
        with_page = [t for t in targets if t["page_id"]]
        ds_writable = [t for t in with_page if not t["数据来源"]]
        written = []
        if mode == "commit":
            notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
            for t in with_page:
                props = {"SERP 占位": {"rich_text": [
                    {"type": "text", "text": {"content": t["SERP 占位"]}}]}}
                if not t["数据来源"]:
                    props["数据来源"] = sc.p_select(ds_value)
                notion.update_page(t["page_id"], props)
                written.append({"action": "update", "query 文本": t["query 文本"],
                                "page_id": t["page_id"],
                                "SERP 占位": t["SERP 占位"],
                                "数据来源": ds_value if not t["数据来源"] else "(已有，未动)"})

        sc.emit(SCRIPT, {
            "script": SCRIPT,
            "mode": mode,
            "status": "ok",
            "wrote_notion": mode == "commit",
            "api_calls": calls,
            "plan": plan,
            "query_db_total": query_db_total,
            "results": results,
            "notion_rows_with_page": len(with_page),
            "notion_ds_writable_rows": len(ds_writable),
            "written": written,
            "schema_note": {
                "现状": "2026-08-06 第四次解冻加了 `SERP 占位` 列（rich_text），"
                        "本脚本每次扫描覆盖写快照；完整占位者名单仍在 logs/serp_scan_*.json",
                "数据来源": "仅在为空时写入「{}」，不覆盖已有来源标注".format(ds_value),
            },
        }, th)
        return sc.EXIT_OK

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
