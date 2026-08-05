#!/usr/bin/env python3
"""AEO Engine · A1 —— 买家原话 → Query 库（候选）。

四条进料链里唯一以**真实对话**为源的一条，也是唯一没有脚本的一条。本脚本补上它。

它做什么：
    读 win/loss 库的「买家原话」列 → 按 config/buyer_quotes.yaml 的规则切子句
    → 命中检索意图 marker 的子句成为 Query 库候选（`数据来源 = 买家原话`）。

它**不**做什么（这些是刻意的，不是没实现）：
  • 不改写任何一个字。写进去的 query 文本必须是买家原话里原样出现过的一段。
    改写一个字，它就不再是买家说过的话，这条链的全部价值就没了。
  • 不调用任何模型去「生成 query」。那是把我们的假设换个说法再写一遍，
    正是这条链要绕开的东西——模型不是市场。
  • 不从关键词工具的 related keywords / people also ask 取词。那是工具的联想，
    不是任何一个真实买家说过的话。两者的 `数据来源` 值不同不是形式问题。
  • 不碰 win/loss 库以外的任何来源。LinkedIn / Reddit 抓到的「信号原文」
    确实是真实的人说的话，但发帖人不是买家，标成「买家原话」就是掺假。
    那条路要走，得先解冻 `数据来源` 加第五个取值——那是真人的决定。

⚠️ 一处装不下的东西（与 SERP 链是同一堵墙）：
   Query 库 7 个冻结字段里没有能装出处的列（`关联资产` 是指向台账的 relation，
   不是指向 win/loss）。所以「每条 query 回溯到具体 win/loss 行」这件事，
   只能落在 logs/buyer_quote_queries_*.json 与 outbox 的审核清单里，
   Notion 侧存不下。同属 Phase 2 §八① 那类待拍板项，本脚本不替真人解决。

两道闸：
  1. 默认 dry-run，写库必须显式 --commit（与 Phase 2 三脚本同一条纪律）。
  2. --commit 还要求 config/buyer_quotes.yaml 的 meta.status == approved。
     marker 词表整份是推演的，没审过就写库 = 拿推演当买家原话。

用法：
    python3 scripts/buyer_quote_queries.py            # dry-run，打印候选与出处
    python3 scripts/buyer_quote_queries.py --review    # 同时把审核清单写进 outbox
    python3 scripts/buyer_quote_queries.py --commit    # 真写 Query 库（需 approved）
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac        # noqa: E402
import aeo_scan as sc          # noqa: E402
# 类型判定复用 keyword_volume 的实现，不另写一份——两份实现必然漂移，
# 而漂移的后果是同一个词在两条链里被打成不同的类型。（只读不改，未动该文件一行。）
from keyword_volume import classify_type   # noqa: E402

SCRIPT = "buyer_quote_queries"


QUOTE_SPAN = re.compile(r'"([^"]{2,})"')


def split_clauses(text, ex):
    """把一条「买家原话」切成子句。切法只做切分与去空白，一个词都不改。

    真人写这一列的格式是：【桶名】"逐字原话" / "逐字原话" 【下一个桶】…
    所以**引号跨度才是天然单位**，不是句号——先取引号跨度，取不到再退回分隔符。
    （2026-08-05 Marvin 那行实测：只按句号切会把两段引语粘成 42 词的一坨，
      然后被 max_words 判掉，看起来像「这段话里没有 query」。
      切错造成的 0 和真的没有造成的 0 长得一模一样，所以这里必须切对。）
    """
    seps = ex["clause_separators"]
    units = []
    if ex.get("quote_spans_first"):
        units = [m.group(1) for m in QUOTE_SPAN.finditer(text)]
    if not units:
        # 退化路径：先把桶名标记也当分隔符，避免【tag】黏在句首
        units = [text]
        for mark in ex.get("bucket_marks") or []:
            units = [seg for u in units for seg in u.split(mark)]

    parts = units
    for sep in seps:
        parts = [seg for p in parts for seg in p.split(sep)]
    return [p.strip() for p in parts if p.strip()]


def hits(text, markers):
    """整词/整短语匹配。子串匹配会把 'we need' 从 'weekend' 里匹出来。"""
    low = " ".join(text.split()).lower()
    for m in markers:
        if re.search(r"(?<!\w){}(?!\w)".format(re.escape(m.lower())), low):
            return m
    return None


def build_candidates(winloss_rows, bq_cfg, scan_cfg, tz):
    """win/loss 行 → 候选。每条带完整出处，出处不全的不产出。"""
    ex = bq_cfg["extract"]
    qcfg = scan_cfg["query"]
    ds_value = "买家原话"          # Phase 0 冻结的 `数据来源` 取值，逐字

    candidates, sources, rejected = [], [], []
    for row in winloss_rows:
        p = row.get("properties", {})
        quote = ac.rich_text(p, "买家原话").strip()
        title = ac.title_text(p, "对话标识")
        company = ac.rich_text(p, "公司")
        seg = ac.select_name(p, "segment")
        dt = ac.date_prop(p, "日期", tz)
        src = {"win_loss_page_id": row["id"], "对话标识": title, "公司": company,
               "segment": seg, "日期": dt.strftime("%Y-%m-%d") if dt else None,
               "买家原话字数": len(quote), "候选数": 0}

        if not quote:
            src["skip_reason"] = "「买家原话」为空——无原话即无产出，不推断、不代写"
            sources.append(src)
            continue

        for idx, clause in enumerate(split_clauses(quote, ex)):
            words = clause.split()
            if not (ex["min_words"] <= len(words) <= ex["max_words"]):
                rejected.append({"clause": clause, "reason": "词数 {} 不在 [{}, {}]".format(
                    len(words), ex["min_words"], ex["max_words"]),
                    "win_loss_page_id": row["id"]})
                continue
            bad = hits(clause, ex["exclude_markers"])
            if bad:
                rejected.append({"clause": clause,
                                 "reason": "命中排除词 {!r}——这是关于我们的句子，"
                                           "不是买家在找什么".format(bad),
                                 "win_loss_page_id": row["id"]})
                continue
            mk = hits(clause, ex["intent_markers"])
            if not mk:
                rejected.append({"clause": clause, "reason": "无检索意图 marker",
                                 "win_loss_page_id": row["id"]})
                continue

            typ = classify_type(clause, scan_cfg)
            candidates.append({
                "query 文本": clause,                    # 逐字，未改写
                "类型": typ,
                "面向角色": qcfg["role_by_type"][typ],
                "月搜索量": None,                        # Phase 0 §4：未知留空
                "数据来源": ds_value,
                "状态": qcfg["initial_status"],          # 「候选」
                "出处": {
                    "win_loss_page_id": row["id"],
                    "对话标识": title, "公司": company, "segment": seg,
                    "日期": src["日期"],
                    "原话逐字": quote,
                    "子句序号": idx,
                    "命中 marker": mk,
                },
            })
            src["候选数"] += 1
        sources.append(src)
    return candidates, sources, rejected


def render(result):
    L = ["🗣 AEO · 买家原话 → Query 库候选 {}".format(result["generated_at"][:16]),
         "",
         "| 项 | 值 |", "|---|---|",
         "| win/loss 行数 | {} |".format(result["counts"]["winloss_rows"]),
         "| 其中「买家原话」非空 | {} |".format(result["counts"]["rows_with_quote"]),
         "| 抽出候选 | {} |".format(result["counts"]["candidates"]),
         "| 与 Query 库重复被跳过 | {} |".format(result["counts"]["skipped_existing"]),
         "| 待写入 | {} |".format(result["counts"]["to_create"]),
         "| 规则文件状态 | `{}` |".format(result["config_status"]),
         ""]

    if result["counts"]["rows_with_quote"] == 0:
        L += ["## 本次零产出，原因不是脚本", "",
              "win/loss {} 行，「买家原话」**一条都没有**——这条链没有燃料。".format(
                  result["counts"]["winloss_rows"]),
              "", "逐行："]
        for s in result["sources"]:
            L.append("- {}（{}）：{}".format(
                s["公司"] or s["对话标识"] or "?", s.get("日期") or "?",
                s.get("skip_reason") or "{} 条候选".format(s["候选数"])))
        L += ["",
              "> 注意触发线的口径。gates.yaml 的 `win_loss_min` 是 5 **场对话**，",
              "> 但这条链吃的是**原话**。零回复的 cold outbound 也记一行 loss，",
              "> 它产出 0 条原话——攒到 5 行仍可能是 0 条。",
              "> 真正的触发线是「有买家原话的 win/loss 行数」，现在是 0。", ""]
    else:
        L += ["## 候选（逐条附出处，供真人逐条审）", ""]
        for c in result["to_create"]:
            s = c["出处"]
            L += ["- **{}**".format(c["query 文本"]),
                  "  - 类型 {} / 面向 {} / 命中 marker `{}`".format(
                      c["类型"], c["面向角色"], s["命中 marker"]),
                  "  - 出处：{} · {} · {} · win/loss `{}`".format(
                      s["公司"] or "?", s["segment"] or "?", s["日期"] or "?",
                      s["win_loss_page_id"]),
                  "  - 原话逐字：{}".format(s["原话逐字"][:400])]
        L.append("")

    L += ["> 写进 Query 库的是**逐字子句**，一个字都没改。",
          "> 出处只落在本次运行日志与本清单里——Query 库 7 个冻结字段装不下它。", ""]
    return "\n".join(L)


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--review", action="store_true",
                        help="把审核清单写进 outbox（由 outbox_sweep 转发）。不写 Notion")
    args = parser.parse_args()
    mode = sc.resolve_mode(args)

    th = ac.load_config("thresholds.yaml")
    try:
        bq_cfg = ac.load_config("buyer_quotes.yaml")
        scan_cfg = ac.load_config("scan.yaml")
        status = bq_cfg["meta"]["status"]

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])

        env = ac.load_env()
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        winloss_rows = notion.query_all(env["DS_WINLOSS"])
        candidates, sources, rejected = build_candidates(
            winloss_rows, bq_cfg, scan_cfg, tz)

        existing = sc.existing_query_texts(notion.query_all(env["DS_QUERY"]))
        to_create, skipped = [], []
        for c in candidates:
            key = sc.norm_query(c["query 文本"])
            if key in existing:
                skipped.append(dict(c, skip_reason="Query 库已有同名 query，跳过不覆盖"))
            else:
                to_create.append(c)
                existing.add(key)

        # 闸②：规则未审就不许写库
        if mode == "commit" and status != "approved":
            raise RuntimeError(
                "config/buyer_quotes.yaml 的 meta.status 是 {!r}，不是 approved。"
                "marker 词表整份是【推演待校准】，未经真人审核不许写库——"
                "否则等于拿推演规则筛出来的句子冒充买家原话。"
                "先跑 --review 出审核清单，审过再把 status 改成 approved。".format(status))

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
                                "win_loss_page_id": c["出处"]["win_loss_page_id"]})

        result = {
            "script": SCRIPT,
            "mode": mode,
            "status": "ok",
            "wrote_notion": mode == "commit",
            "config_status": status,
            "generated_at": datetime.now(tz).isoformat(),
            "counts": {
                "winloss_rows": len(winloss_rows),
                "rows_with_quote": sum(1 for s in sources if not s.get("skip_reason")),
                "candidates": len(candidates),
                "skipped_existing": len(skipped),
                "to_create": len(to_create),
                "clauses_rejected": len(rejected),
            },
            "sources": sources,
            "to_create": to_create,
            "skipped_existing": skipped,
            "clauses_rejected": rejected,
            "written": written,
            "schema_gap": {
                "问题": "Query 库 7 个冻结字段没有能装出处的列"
                        "（关联资产是指向台账的 relation，不是指向 win/loss）",
                "本次处置": "出处只落 logs/{}_*.json 与 outbox 审核清单".format(SCRIPT),
                "待拍板": "与 SERP 链是同一堵墙，同属 Phase 2 §八① 那类问题，不在本脚本解决",
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
