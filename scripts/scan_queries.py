#!/usr/bin/env python3
"""AEO Engine · A1 —— 扫描抓到的真实原话 → Query 库（`数据来源 = A1 扫描`）。

Shawn 2026-08-05 指定新建。六条进料链里最后一条「有取值无脚本」的补上了。

为什么这条链值得建：
    Reddit 存量提问帖的**标题本身就是一个活人打出来的问句**。
    不是我们假设的（探测问题）、不是模型联想的（AI 建议）、
    不是工具关联的（Keyword Planner 扩展建议）——
    是一个真人在真的想解决这个问题时，真的敲出来的那句话。

    而 scan_reddit_weekly.md §5 早就写死了「`信号原文` = 【帖型：X】+ 原文照抄，
    不改写、不翻译、不概括」。**料一直在进，只是过去没有管子通到 Query 库。**
    2026-08-05 additive 解冻加了 `A1 扫描` 这个取值之后，这根管子才有地方接。

出处比买家原话那条链更硬：水箱的 `来源链接` 是帖子永久链接，一个公网 URL——
任何人都能点开核对这句话是不是真有人说过。

它**不**做什么：
  • 不改写任何一个字（与买家原话链同一条硬约束）。
  • 不取 Apollo 与手动录入的行。Apollo 行的 `信号原文` 是名单条件，
    它自己就写着「非原文引用：本行来源是名单条件而非本人发言」——
    把那个当买家语言写进 Query 库就是掺假。
  • 不调模型、不调任何计费 API、不做任何对外请求。

⚠️ **本脚本的全部规则未经真实数据验证。**
   水箱当前 `来源 = A1 扫描` 是 0 行，Reddit 周批扫一轮都还没跑过。
   关于「`信号原文` 长什么样」的假设（帖型前缀、第一行是标题）
   全部照 playbook 的字面规定写，没在真实行上对过。
   首轮周批扫出料之后**必须回来复核**，config 的 meta.unverified 也标着这件事。

两道闸（与另两条链一致）：
  1. 默认 dry-run，写库必须显式 --commit。
  2. --commit 还要求 config/scan_queries.yaml 的 meta.status == approved。

用法：
    python3 scripts/scan_queries.py             # dry-run，打印候选与出处
    python3 scripts/scan_queries.py --review    # 同时把审核清单写进 outbox
    python3 scripts/scan_queries.py --commit    # 真写 Query 库（需 approved）
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402
# 类型判定复用 keyword_volume 的实现，不另写一份（只读不改，未动该文件一行）。
from keyword_volume import classify_type   # noqa: E402

SCRIPT = "scan_queries"
SOURCE_FILTER = "A1 扫描"      # 水箱 `来源` 的取值，Phase 0 冻结，逐字


def hits(text, markers):
    """整词/整短语匹配。子串匹配会把 'we need' 从 'weekend' 里匹出来。"""
    low = " ".join(text.split()).lower()
    for m in markers:
        if re.search(r"(?<!\w){}(?!\w)".format(re.escape(m.lower())), low):
            return m
    return None


def split_units(signal_text, ex):
    """`信号原文` → (帖型, 标题, [正文子句])。只切分与去空白，一个词不改。

    形态假设照 scan_reddit_weekly.md §4/§5 的字面规定：
      【帖型：工具求推荐】<原文照抄>
    第一行当标题——Reddit 的标题就是那句问话，正文多是背景交代。
    LinkedIn 侧的行没有帖型前缀，正则不命中就当没有，不报错。
    """
    text = signal_text.strip()
    post_type = None
    m = re.search(ex["post_type_prefix_pattern"], text)
    if m:
        post_type = m.group(1)
        text = text[m.end():].strip()

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    title = lines[0] if (ex.get("title_first") and lines) else None
    body = lines[1:] if title else lines

    parts = body
    for sep in ex["clause_separators"]:
        parts = [seg for p in parts for seg in p.split(sep)]
    return post_type, title, [p.strip() for p in parts if p.strip()]


def build_candidates(pipeline_rows, cfg, scan_cfg, tz):
    ex, caps = cfg["extract"], cfg["caps"]
    qcfg = scan_cfg["query"]
    ds_value = cfg["data_source_value"]

    candidates, sources, rejected = [], [], []
    for row in pipeline_rows:
        p = row.get("properties", {})
        if ac.select_name(p, "来源") != SOURCE_FILTER:
            continue
        raw = ac.rich_text(p, "信号原文").strip()
        name = ac.title_text(p, "人名")
        link = (p.get("来源链接") or {}).get("url")
        seg = ac.select_name(p, "segment")
        sig = ac.select_name(p, "信号类型")
        dt = ac.date_prop(p, "入箱日期", tz)
        src = {"pipeline_page_id": row["id"], "人名": name, "来源链接": link,
               "segment": seg, "信号类型": sig,
               "入箱日期": dt.strftime("%Y-%m-%d") if dt else None,
               "信号原文字数": len(raw), "候选数": 0}

        if not raw:
            src["skip_reason"] = "「信号原文」为空——无原文即无产出，不推断、不代写"
            sources.append(src)
            continue
        if not link:
            # 来源链接是这条链唯一的硬出处。没有它就无法回溯，宁可不产出。
            src["skip_reason"] = "「来源链接」为空——无出处不写"
            sources.append(src)
            continue

        post_type, title, body = split_units(raw, ex)
        src["帖型"] = post_type

        # 标题与正文子句分开判：标题允许更长（真实 Reddit 标题常 15–20 词，
        # 用正文的 max_words 卡会把最有价值的那一句判掉）。
        units = ([(title, "标题", ex["title_max_words"])] if title else []) \
            + [(c, "正文", ex["max_words"]) for c in body]

        for idx, (clause, where, cap_words) in enumerate(units):
            if src["候选数"] >= caps["max_per_row"]:
                rejected.append({"clause": clause, "where": where,
                                 "reason": "本行已达 caps.max_per_row={}".format(
                                     caps["max_per_row"]),
                                 "pipeline_page_id": row["id"]})
                continue
            words = clause.split()
            if not (ex["min_words"] <= len(words) <= cap_words):
                rejected.append({"clause": clause, "where": where,
                                 "reason": "词数 {} 不在 [{}, {}]".format(
                                     len(words), ex["min_words"], cap_words),
                                 "pipeline_page_id": row["id"]})
                continue
            bad = hits(clause, ex["exclude_markers"])
            if bad:
                rejected.append({"clause": clause, "where": where,
                                 "reason": "命中排除词 {!r}".format(bad),
                                 "pipeline_page_id": row["id"]})
                continue
            mk = hits(clause, ex["intent_markers"])
            if not mk:
                rejected.append({"clause": clause, "where": where,
                                 "reason": "无检索意图 marker",
                                 "pipeline_page_id": row["id"]})
                continue

            typ = classify_type(clause, scan_cfg)
            candidates.append({
                "query 文本": clause,                  # 逐字，未改写
                "类型": typ,
                "面向角色": qcfg["role_by_type"][typ],
                "月搜索量": None,                      # Phase 0 §4：未知留空
                "数据来源": ds_value,
                "状态": qcfg["initial_status"],        # 「候选」
                "出处": {
                    "pipeline_page_id": row["id"], "来源链接": link,
                    "人名": name, "segment": seg, "信号类型": sig,
                    "帖型": post_type, "取自": where, "子句序号": idx,
                    "命中 marker": mk, "信号原文逐字": raw,
                },
            })
            src["候选数"] += 1
        sources.append(src)
    return candidates, sources, rejected


def render(r):
    c = r["counts"]
    L = ["🧵 AEO · A1 扫描原话 → Query 库候选 {}".format(r["generated_at"][:16]),
         "",
         "| 项 | 值 |", "|---|---|",
         "| 水箱总行数 | {} |".format(c["pipeline_rows"]),
         "| 其中 `来源 = A1 扫描` | {} |".format(c["a1_rows"]),
         "| 抽出候选 | {} |".format(c["candidates"]),
         "| 与 Query 库重复 | {} |".format(c["skipped_existing"]),
         "| **待写入** | **{}** |".format(c["to_create"]),
         "| 被 max_per_run 截下 | {} |".format(c["capped"]),
         "| 规则文件状态 | `{}` |".format(r["config_status"]),
         ""]

    if c["a1_rows"] == 0:
        L += ["## 本次零产出，原因不是脚本", "",
              "水箱 {} 行里，`来源 = A1 扫描` 的是 **0 条**——".format(c["pipeline_rows"]),
              "Reddit / LinkedIn 周批扫一轮都还没跑过，这条链没有燃料。",
              "",
              "> 现有 {} 行的来源是 `{}`。Apollo 那批刻意不取：".format(
                  c["pipeline_rows"], r["source_distribution"]),
              "> 它们的 `信号原文` 自己写着「非原文引用：本行来源是名单条件而非本人发言」，",
              "> 当买家语言写进 Query 库就是掺假。",
              "",
              "**这也意味着本脚本的抽取规则一条都没在真实数据上验证过。**",
              "首轮周批扫出料之后必须回来复核 `config/scan_queries.yaml`。", ""]
    else:
        L += ["## 候选（逐条附出处，供真人逐条审）", ""]
        for it in r["to_create"]:
            s = it["出处"]
            L += ["- **{}**".format(it["query 文本"]),
                  "  - {} / {} · 取自{} · 帖型 {} · 命中 `{}`".format(
                      it["类型"], it["面向角色"], s["取自"],
                      s["帖型"] or "（无标注）", s["命中 marker"]),
                  "  - 出处：{} · {} · {}".format(
                      s["人名"] or "?", s["segment"] or "?", s["来源链接"])]
        L.append("")

    L += ["> 写进 Query 库的是**逐字原文**，一个字都没改。",
          "> 出处（来源链接、水箱行 id、帖型）只落日志与本清单——",
          "> Query 库 7 个冻结字段装不下，与另两条链撞的是同一堵墙。", ""]
    return "\n".join(L)


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--review", action="store_true",
                        help="把审核清单写进 outbox（由 outbox_sweep 转发）。不写 Notion")
    args = parser.parse_args()
    mode = sc.resolve_mode(args)

    th = ac.load_config("thresholds.yaml")
    try:
        cfg = ac.load_config("scan_queries.yaml")
        scan_cfg = ac.load_config("scan.yaml")
        status = cfg["meta"]["status"]

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])

        env = ac.load_env()
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        pipeline = notion.query_all(env["DS_PIPELINE"])

        dist = {}
        for row in pipeline:
            s = ac.select_name(row.get("properties", {}), "来源") or "(空)"
            dist[s] = dist.get(s, 0) + 1

        candidates, sources, rejected = build_candidates(pipeline, cfg, scan_cfg, tz)

        existing = sc.existing_query_texts(notion.query_all(env["DS_QUERY"]))
        fresh, skipped = [], []
        for c in candidates:
            key = sc.norm_query(c["query 文本"])
            if key in existing:
                skipped.append(dict(c, skip_reason="Query 库已有同名 query，跳过不覆盖"))
            else:
                fresh.append(c)
                existing.add(key)

        cap = cfg["caps"]["max_per_run"]
        to_create, capped = fresh[:cap], fresh[cap:]

        if mode == "commit" and status != "approved":
            raise RuntimeError(
                "config/scan_queries.yaml 的 meta.status 是 {!r}，不是 approved。"
                "本文件的抽取规则**一条都没在真实数据上验证过**"
                "（水箱 `来源 = A1 扫描` 当前 0 行）。"
                "先跑首轮周批扫拿到真实行、--review 出审核清单、真人对着真实数据复核规则，"
                "再改 approved。".format(status))

        written = []
        if mode == "commit":
            for c in to_create:
                page = notion.create_page(env["DS_QUERY"], {
                    "query 文本": sc.p_title(c["query 文本"]),
                    "类型": sc.p_select(c["类型"]),
                    "面向角色": sc.p_select(c["面向角色"]),
                    "月搜索量": sc.p_number(c["月搜索量"]),
                    "数据来源": sc.p_select(c["数据来源"]),
                    "状态": sc.p_select(c["状态"]),
                })
                written.append({"action": "create", "query 文本": c["query 文本"],
                                "page_id": page["id"],
                                "来源链接": c["出处"]["来源链接"],
                                "pipeline_page_id": c["出处"]["pipeline_page_id"]})

        a1_rows = sum(1 for s in sources)
        result = {
            "script": SCRIPT, "mode": mode, "status": "ok",
            "wrote_notion": mode == "commit",
            "config_status": status,
            "config_unverified": cfg["meta"].get("unverified"),
            "generated_at": datetime.now(tz).isoformat(),
            "source_distribution": dist,
            "counts": {
                "pipeline_rows": len(pipeline),
                "a1_rows": a1_rows,
                "candidates": len(candidates),
                "skipped_existing": len(skipped),
                "to_create": len(to_create),
                "capped": len(capped),
                "clauses_rejected": len(rejected),
            },
            "sources": sources,
            "to_create": to_create,
            "capped": capped,
            "skipped_existing": skipped,
            "clauses_rejected": rejected,
            "written": written,
            "schema_gap": {
                "问题": "Query 库 7 个冻结字段没有能装出处的列",
                "本次处置": "来源链接与水箱行 id 只落 logs/{}_*.json 与审核清单".format(SCRIPT),
                "待拍板": "与 SERP、买家原话、AI 建议三条链撞的是同一堵墙",
            },
        }
        body = render(result)
        result["report"] = body
        sc.emit(SCRIPT, result, th)
        print(body, file=sys.stderr)

        if args.review:
            path = ac.write_outbox(
                "{}_{}.md".format(SCRIPT, datetime.now(tz).strftime("%Y-%m-%d")), body)
            print("PUSH: {}".format(path))
        return sc.EXIT_OK

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
