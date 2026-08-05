#!/usr/bin/env python3
"""AEO Engine · A1 —— AI 追问建议的 query → 候选池 → Query 库。

Shawn 2026-08-05 指定新增的两条进料路径之一。

链路：
    probe_ai_engines_daily.md 每日探测跑完 → 在同一对话里追问一句
    「跟刚才那条相关的问题人们还常问哪些」 → 逐字收下它给的问法
    → 本脚本摄入 → 落 data/query_candidates.jsonl（出处留档）
    → --commit 写 Query 库（`数据来源 = AI 建议`、`状态 = 候选`、月搜索量留空）

**这条链是假设源，不是证据源。** 模型不是市场：它说人们常这么问，不等于有人这么搜过。
真正的证据是 Keyword Planner 回来的月搜索量。Shawn 拍板候选直接进库、量后补，
所以 Query 库里会同时躺着「有人真搜过的词」和「模型说会有人搜的词」——
两者靠 `数据来源` 分开（Phase 0 字段表 2026-08-05 解冻，additive 加了两个取值）。
排 AEO 内容优先级时必须先看那一列。

playbook 不写 Query 库（那是它逐字保留的红线），所以写库这一步在本脚本，不在 playbook。

输入（两条路，同 scan_log_append.py 的形态）：
    正常路径 —— 浏览器会话能落盘时，直接追加进 data/query_candidates.jsonl
    降级路径 —— 落不了盘时（Phase 2 §八④ 已知情形），上报里贴 JSON，真人管道喂进来

    echo '{"date":"2026-08-06","engine":"ChatGPT","question_set":"任务式 pain feeler",
           "asked_after":"how to find a clip in hours of footage",
           "suggestions":["...","..."]}' | python3 scripts/query_candidates.py

用法：
    python3 scripts/query_candidates.py                  # dry-run：什么都不写，只算给你看
    python3 scripts/query_candidates.py --stage          # 进候选池，不写 Notion（每日常规）
    python3 scripts/query_candidates.py --commit         # 进池 + 写 Query 库
    python3 scripts/query_candidates.py --pool-only      # 只看候选池现状
    python3 scripts/query_candidates.py --pool --commit  # 把池子里未入库的写进 Query 库
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402
# 类型判定复用 keyword_volume 的实现，不另写一份（只读不改，未动该文件一行）。
from keyword_volume import classify_type   # noqa: E402

SCRIPT = "query_candidates"
POOL = os.path.join(ac.DATA_DIR, "query_candidates.jsonl")


def load_pool():
    """append-only 候选池。坏行不吞：解析失败整体抛，不跳过。"""
    if not os.path.exists(POOL):
        return []
    out = []
    with open(POOL, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError as exc:
                raise ValueError("{} 第 {} 行不是合法 JSON：{}".format(
                    os.path.relpath(POOL, ac.REPO), n, exc))
    return out


def append_pool(items):
    with open(POOL, "a", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")


def hits(text, markers):
    low = " ".join(text.split()).lower()
    for m in markers:
        if re.search(r"(?<!\w){}(?!\w)".format(re.escape(m.lower())), low):
            return m
    return None


def normalize_input(doc, scan_cfg):
    """输入 → 逐条建议。缺字段就报错，不补默认值（同 scan_log_append 的口径）。"""
    records = doc if isinstance(doc, list) else (doc.get("records") or [doc])
    engines = set(scan_cfg["probe"]["engines"])
    sets = set(scan_cfg["probe"]["question_sets"])

    out = []
    for rec in records:
        for key in ("date", "engine", "question_set", "asked_after", "suggestions"):
            if not rec.get(key):
                raise ValueError("记录缺字段 {!r}：{}".format(
                    key, json.dumps(rec, ensure_ascii=False)[:300]))
        if rec["engine"] not in engines:
            raise ValueError("engine {!r} 不在 scan.yaml probe.engines {} 里".format(
                rec["engine"], sorted(engines)))
        if rec["question_set"] not in sets:
            raise ValueError("question_set {!r} 不在 scan.yaml probe.question_sets {} 里"
                             .format(rec["question_set"], sorted(sets)))
        if not isinstance(rec["suggestions"], list):
            raise ValueError("suggestions 必须是数组")
        for idx, s in enumerate(rec["suggestions"]):
            if not isinstance(s, str):
                raise ValueError("suggestions 里必须全是字符串，第 {} 项是 {}".format(
                    idx, type(s).__name__))
            out.append({
                "query 文本": " ".join(s.split()),      # 只压空白，不改写任何一个词
                "date": rec["date"], "engine": rec["engine"],
                "question_set": rec["question_set"],
                "asked_after": rec["asked_after"], "rank": idx,
            })
    return out


def screen(items, cfg):
    """按 filters 与 caps.max_per_ask 筛。被筛掉的**逐条说明理由**，不静默丢。"""
    f, caps = cfg["filters"], cfg["caps"]
    kept, dropped = [], []
    per_ask = {}
    for it in items:
        words = it["query 文本"].split()
        if not (f["min_words"] <= len(words) <= f["max_words"]):
            dropped.append(dict(it, drop_reason="词数 {} 不在 [{}, {}]".format(
                len(words), f["min_words"], f["max_words"])))
            continue
        bad = hits(it["query 文本"], f["exclude_markers"])
        if bad:
            dropped.append(dict(it, drop_reason="命中排除词 {!r}——这是模型的解释性文字，"
                                                "不是提问".format(bad)))
            continue
        key = (it["date"], it["engine"], it["question_set"])
        per_ask[key] = per_ask.get(key, 0) + 1
        if per_ask[key] > caps["max_per_ask"]:
            dropped.append(dict(it, drop_reason="超过 caps.max_per_ask={}——"
                                                "模型排在后面的是在凑数，不是它观察到的高频"
                                                .format(caps["max_per_ask"])))
            continue
        kept.append(it)
    return kept, dropped


def render(r):
    c = r["counts"]
    L = ["🤖 AEO · AI 建议 query 候选 {}".format(r["generated_at"][:16]),
         "",
         "| 项 | 值 |", "|---|---|",
         "| 本次读入建议 | {} |".format(c["ingested"]),
         "| 过滤后留下 | {} |".format(c["screened_kept"]),
         "| 与 Query 库/池子重复 | {} |".format(c["duplicate"]),
         "| 新进候选池 | {} |".format(c["pool_added"]),
         "| 候选池累计 | {} |".format(c["pool_total"]),
         "| **待写 Query 库** | **{}** |".format(c["to_create"]),
         "| 被日上限截下 | {} |".format(c["capped"]),
         ""]

    if r["to_create"]:
        L += ["## 待写入（`数据来源 = AI 建议` · `状态 = 候选` · 月搜索量留空）", ""]
        for it in r["to_create"]:
            L.append("- **{}** — {} / {} · 由 `{}` 追问得来 · {} 第 {} 条".format(
                it["query 文本"], it["类型"], it["面向角色"],
                it["asked_after"], it["engine"], it["rank"] + 1))
        L.append("")

    if r["capped"]:
        L += ["## 被 `caps.max_per_day={}` 截下（留在池子里，没丢）".format(
            r["cap_per_day"]), ""]
        for it in r["capped"]:
            L.append("- {}".format(it["query 文本"]))
        L.append("")

    if r["dropped"]:
        L += ["## 过滤掉的", ""]
        for it in r["dropped"]:
            L.append("- {!r} → {}".format(it["query 文本"][:120], it["drop_reason"]))
        L.append("")

    L += ["> ⚠️ 这些是**假设**，不是证据。模型说人们常这么问，不等于有人这么搜过。",
          "> 月搜索量为空 + `数据来源 = AI 建议` = 一条还没有任何市场证据的词。",
          "> 排 AEO 内容优先级时先看那一列；真实证据要等 Keyword Planner 回来。", ""]
    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="只计算与输出，不写 Notion（默认）")
    g.add_argument("--commit", dest="commit", action="store_true",
                   help="真正写入 Query 库。不给这个参数就绝不写库")
    parser.add_argument("--file", help="从文件读 JSON，默认从 stdin 读")
    parser.add_argument("--stage", action="store_true",
                        help="把新条目追加进候选池 data/query_candidates.jsonl，"
                             "但**不写 Notion**。每日探测的常规落盘用这个")
    parser.add_argument("--pool", action="store_true",
                        help="不读新输入，改为把候选池里尚未入库的写进 Query 库")
    parser.add_argument("--pool-only", action="store_true",
                        help="只打印候选池现状，不读输入也不写任何东西")
    args = parser.parse_args()
    mode = sc.resolve_mode(args)

    th = ac.load_config("thresholds.yaml")
    try:
        cfg = ac.load_config("query_candidates.yaml")
        scan_cfg = ac.load_config("scan.yaml")
        ds_value = cfg["data_source_value"]
        qcfg = scan_cfg["query"]

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        pool = load_pool()

        if args.pool_only:
            sc.emit(SCRIPT, {"script": SCRIPT, "status": "pool_only",
                             "wrote_notion": False, "pool_total": len(pool),
                             "pool": pool}, th)
            return sc.EXIT_OK

        # ---- 读入 ----
        if args.pool:
            ingested, dropped = list(pool), []
        else:
            raw = (open(args.file, encoding="utf-8").read() if args.file
                   else sys.stdin.read())
            if not raw.strip():
                raise ValueError("没有输入。用 --file 指定文件，把 JSON 从管道喂进来，"
                                 "或用 --pool 处理已在候选池里的条目。")
            ingested, dropped = screen(normalize_input(json.loads(raw), scan_cfg), cfg)

        # ---- 去重：对 Query 库 + 对候选池 ----
        env = ac.load_env()
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        in_db = sc.existing_query_texts(notion.query_all(env["DS_QUERY"]))
        in_pool = {sc.norm_query(p["query 文本"]) for p in pool}

        fresh, duplicate = [], []
        seen = set()
        for it in ingested:
            key = sc.norm_query(it["query 文本"])
            if key in in_db:
                duplicate.append(dict(it, dup_reason="Query 库已有同名 query"))
            elif key in seen or (key in in_pool and not args.pool):
                duplicate.append(dict(it, dup_reason="候选池已有同名 query"))
            else:
                seen.add(key)
                fresh.append(it)

        # ---- 新条目进池 ----
        # 进池也是**写**，所以它同样受 dry-run 管：不带参数跑一次不该改变任何状态。
        # 这条是 Phase 3 §七④ 的教训——一次只读的诊断跑把当天的权威 plan 覆盖了，
        # 「诊断」和「产线」共用一条写路径迟早出这种事。
        #   默认      = 什么都不写，只算给你看
        #   --stage   = 进池，不写 Notion
        #   --commit  = 进池 + 写 Query 库
        pool_added = []
        if not args.pool and fresh and (args.stage or mode == "commit"):
            stamp = datetime.now(tz).isoformat()
            pool_added = [dict(it, ingested_at=stamp) for it in fresh]
            append_pool(pool_added)

        # ---- 日上限 ----
        cap = cfg["caps"]["max_per_day"]
        today = datetime.now(tz).strftime("%Y-%m-%d")
        already = sum(1 for p in pool if p.get("committed_on") == today)
        room = max(0, cap - already)
        to_create_raw, capped = fresh[:room], fresh[room:]

        for it in to_create_raw:
            it["类型"] = classify_type(it["query 文本"], scan_cfg)
            it["面向角色"] = qcfg["role_by_type"][it["类型"]]

        written = []
        if mode == "commit":
            for it in to_create_raw:
                page = notion.create_page(env["DS_QUERY"], {
                    "query 文本": sc.p_title(it["query 文本"]),
                    "类型": sc.p_select(it["类型"]),
                    "面向角色": sc.p_select(it["面向角色"]),
                    "月搜索量": sc.p_number(None),      # Phase 0 §4：未知留空
                    "数据来源": sc.p_select(ds_value),
                    "状态": sc.p_select(qcfg["initial_status"]),
                })
                written.append({"action": "create", "query 文本": it["query 文本"],
                                "page_id": page["id"], "engine": it["engine"],
                                "asked_after": it["asked_after"]})
            if written:
                append_pool([{"query 文本": w["query 文本"], "committed_on": today,
                              "page_id": w["page_id"], "engine": w["engine"],
                              "asked_after": w["asked_after"],
                              "note": "已写入 Query 库"} for w in written])

        result = {
            "script": SCRIPT, "mode": mode, "status": "ok",
            "wrote_notion": mode == "commit",
            "generated_at": datetime.now(tz).isoformat(),
            "config_status": cfg["meta"]["status"],
            "cap_per_day": cap,
            "counts": {
                "ingested": len(ingested) + len(dropped),
                "screened_kept": len(ingested),
                "screened_dropped": len(dropped),
                "duplicate": len(duplicate),
                "pool_added": len(pool_added),
                "pool_total": len(pool) + len(pool_added),
                "to_create": len(to_create_raw),
                "capped": len(capped),
            },
            "to_create": to_create_raw,
            "capped": capped,
            "dropped": dropped,
            "duplicate": duplicate,
            "written": written,
            "provenance_note": "Query 库 7 个冻结字段装不下「哪个引擎、哪天、追问哪条问题」，"
                               "出处只落 data/query_candidates.jsonl 与本次日志。",
            "known_issue": "keyword_volume.py:299-301 补搜索量时会把 `数据来源` 覆写成"
                           "「Keyword Planner」，抹掉本链标的「AI 建议」。"
                           "KP 环节上线前必须先处置。",
        }
        body = render(result)
        result["report"] = body
        sc.emit(SCRIPT, result, th)
        print(body, file=sys.stderr)
        return sc.EXIT_OK

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
