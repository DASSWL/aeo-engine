#!/usr/bin/env python3
"""AEO Engine · J1 —— Query 库选题 → 证据配对 → AEO 内容草稿 → 台账登记。

依据:Build Spec · Phase 3「J1 证据生产」+ 2026-08-06 Shawn 拍板(建立流程、计入台账)。

三段式,与 J4 draft_runner 同构,理由也相同(LLM 不进 Python):
  1. plan     —— 纯 Python。读 Query 库排选题(有量优先)、筛证据候选、写 claude prompt
  2. (claude) —— 由 run_j1_draft.sh 调 claude -p,加载真 skill 写英文正文
  3. assemble —— 纯 Python。落 outbox 文章 + 通知消息;--commit 时经 j1_evidence.py
     登记台账(状态草稿),写后独立回读核对

写台账走且只走 j1_evidence.py 子进程——单一写路径,五道闸在那边,本脚本不复制闸门逻辑。
签发永远是真人动作:台账状态停在「草稿」,J2 的 CI lint 拒绝未签发内容上线。

用法:
    python3 scripts/j1_runner.py                              # dry-run,只算队列
    python3 scripts/j1_runner.py --emit-prompt logs/p.md      # plan 并写出 prompt
    python3 scripts/j1_runner.py --assemble logs/out.txt --commit
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402
import skill_check as sk     # noqa: E402

SCRIPT = "j1_runner"

DRAFT_BEGIN = "===DRAFT {}==="
DRAFT_END = "===END {}==="
REFUSE_BEGIN = "===REFUSE {}==="


# --------------------------------------------------------------------------
# plan:选题与证据候选
# --------------------------------------------------------------------------

def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "untitled"


def pick_queries(queries, ledger, cfg):
    """选题队列。返回 (选中, 逐条落选原因)。

    排除已有台账行的 query(按 sc.norm_query 归一后比对台账「面向」)——
    重复登记同一选题会让签发队列出现两行指同一篇。
    """
    q = cfg["queue"]
    ledger_facing = {sc.norm_query(ac.rich_text(r.get("properties", {}), "面向"))
                     for r in ledger}

    picked, skipped = [], []
    for row in queries:
        p = row.get("properties", {})
        text = ac.title_text(p, "query 文本")
        qtype = ac.select_name(p, "类型")
        status = ac.select_name(p, "状态")
        if qtype not in q["types_allowed"]:
            continue  # 类型不在产线范围,连落选都不算,不噪音
        if ac.select_name(p, "数据来源") not in q["sources_allowed"]:
            continue  # 补量/占位来源(KP、SERP)不是选题来源,理由见 j1.yaml
        if status in q["statuses_exclude"]:
            skipped.append({"query": text, "reason": "状态={}".format(status)})
            continue
        if sc.norm_query(text) in ledger_facing:
            skipped.append({"query": text, "reason": "台账已有同选题行"})
            continue
        picked.append({
            "slug": slugify(text),
            "query": text,
            "row_id": row.get("id"),
            "面向角色": ac.select_name(p, "面向角色"),
            "数据来源": ac.select_name(p, "数据来源"),
            "月搜索量": (p.get("月搜索量") or {}).get("number"),
            "搜索量区间": ac.rich_text(p, "搜索量区间"),
        })

    # 有量优先,同 j1_evidence.query_priority 的口径;区间字符串次之;其余保持原序
    picked.sort(key=lambda r: (r["月搜索量"] is None,
                               -(r["月搜索量"] or 0),
                               not r["搜索量区间"]))

    # slug 撞车防线:两个 query 归一后可能同 slug,加序号,宁可难看不可覆盖
    seen = {}
    for it in picked:
        if it["slug"] in seen:
            seen[it["slug"]] += 1
            it["slug"] = "{}-{}".format(it["slug"], seen[it["slug"]])
        else:
            seen[it["slug"]] = 1

    return picked[:q["max_per_run"]], skipped


def evidence_candidates(pipeline, cfg):
    """证据候选:水箱里有真实原话的行 → SIG 编号 + 原话。

    带 exclude_markers(如「非原文引用」)的行不进候选——J4 对同类行的判定
    是「名单命中条件,不是他说的话」,内容产线同样不许拿它当痛点证据。
    prompt 里刻意不给人名与链接:证据只用来定痛点形态,正文不许指认任何人。
    """
    ev = cfg["evidence"]
    import hashlib
    out = []
    for row in pipeline:
        p = row.get("properties", {})
        quote = ac.rich_text(p, "信号原文").strip()
        url = (p.get("来源链接") or {}).get("url") or ""
        if not quote or not url:
            continue
        if any(m in quote for m in ev["exclude_markers"]):
            continue
        code = "SIG-" + hashlib.sha1(
            sc.norm_url(url).encode("utf-8")).hexdigest()[:8]
        out.append({"code": code, "row_id": row.get("id"),
                    "quote": quote[:ev["quote_max_chars"]]})
    # 同一链接多行 → 同一 SIG,去重留第一条
    seen, deduped = set(), []
    for c in out:
        if c["code"] in seen:
            continue
        seen.add(c["code"])
        deduped.append(c)
    return deduped[:ev["max_candidates_in_prompt"]]


def confirmed_facts(cfg):
    """facts.json 里 status=已确认 的字段 → prompt 用的事实清单。

    只认「已确认」。待真人补的字段视同不存在——facts.json 的 hard_rule
    是台账 lint 的基准,产线引用了未确认字段等于让 lint 去守护假话。
    """
    with open(os.path.expanduser(cfg["facts_file"]), encoding="utf-8") as fh:
        facts = json.load(fh)

    lines = []

    def walk(node, label):
        if isinstance(node, dict):
            if node.get("status") == "已确认" and node.get("value"):
                lines.append("- {}: {}".format(label, node["value"]))
                return
            for k, v in node.items():
                if k.startswith("_"):
                    continue
                walk(v, "{}.{}".format(label, k) if label else k)
        elif isinstance(node, list):
            for item in node:
                walk(item, label)

    for section in ("claims", "integrations", "anchor_terms"):
        walk(facts.get(section, {}), section)

    negatives = []
    if not facts.get("pricing", {}).get("public_pricing_exists", False):
        negatives.append("There is NO public pricing. Do not invent or imply "
                         "any price, tier, or 'affordable' claim.")
    negatives.append("There are NO confirmed benchmarks (accuracy, speed, "
                     "time saved). Do not use any number about performance.")
    negatives.append("There are NO named customers or quotable customer "
                     "results. Do not invent testimonials or case studies.")
    return lines, negatives


def build_prompt(items, candidates, cfg, skill_report):
    fact_lines, negatives = confirmed_facts(cfg)
    art = cfg["article"]
    missing = skill_report.get("missing_required") or []

    lines = [
        "# 任务:为 Vivu 写 AEO 内容(痛点级 query 的回答文章)",
        "",
        "文章发布在 vivu.ai,目标读者是带着下面这些问题来搜索的人。",
        "你只写草稿;发布前有真人签发,你不发布任何东西。",
        "",
        "## 强制产线",
        "",
        "写任何一个字的英文之前,**先调用 ai-writing-guideline skill**"
        "(它会指向实时规则文件 ai_writings.md,每次读新的,不要用记忆里的版本)。",
        "这不是建议。这一步跳过,产出一律作废。",
        "",
    ]
    if missing:
        lines += [
            "## ⚠️ 缺失的 skill",
            "",
            "以下 skill 本机不存在:{}。不要自己编一套顶替;"
            "正文照写,但在文末标注缺了什么。".format("、".join(missing)),
            "",
        ]
    lines += [
        "## 产品事实(唯一可用的事实集合,一个字都不许超出)",
        "",
        "以下来自站点事实层 facts.json,只含已确认字段:",
        "",
    ]
    lines += fact_lines
    lines += [""]
    for n in negatives:
        lines.append("- ⛔ {}".format(n))
    lines += [
        "",
        "## 证据候选(真实买家痛点,决定文章的痛点形态)",
        "",
        "每条是水箱里一行真实信号的原话摘录。规则:",
        "- 每篇文章从下面挑 1-3 条与该 query 痛点形态吻合的证据,"
        "在 EVIDENCE 行写出它们的编号。",
        "- 证据只用来校准痛点写法。**正文不许出现当事人身份、公司名或原话直引**。",
        "- 一条都配不上的 query 不要硬写:输出 REFUSE 包说明原因。"
        "没有证据的内容只能靠编,而编造正是这条产线的闸门要防的。",
        "",
    ]
    for c in candidates:
        lines.append("- {}: {}".format(c["code"], c["quote"]))
    lines += [
        "",
        "## 文章要求",
        "",
        "- 语言:{}。长度 {} 词。".format(art["language"], art["word_range"]),
        "- H1 即回答:标题直接承接 query 的问法。",
        "- 第一段给出直接答案(answer-engine 会截取它),然后再展开。",
        "- 诚实展开所有路线,包括不用 Vivu 的路线(手动、DIY、别的工具形态)。",
        "- 自然的位置放一段「什么情况下你其实不需要这类工具」。",
        "- 结尾 CTA 固定用这句事实:{}".format(art["demo_cta"]),
        "- 标题 sentence case;不用 emoji;少用 em dash。",
        "",
        "## 输出格式(严格遵守,assemble 按此解析)",
        "",
        "每篇文章一个包,包之间不要任何其他文字:",
        "",
        "```",
        DRAFT_BEGIN.format("<slug>"),
        "EVIDENCE: SIG-xxxxxxxx, SIG-yyyyyyyy",
        "TITLE: <文章标题>",
        "<markdown 正文,从 H1 开始>",
        DRAFT_END.format("<slug>"),
        "```",
        "",
        "配不上证据时:",
        "",
        "```",
        REFUSE_BEGIN.format("<slug>"),
        "<一句话原因>",
        DRAFT_END.format("<slug>"),
        "```",
        "",
        "## 本次选题",
        "",
    ]
    for it in items:
        vol = it["月搜索量"] if it["月搜索量"] is not None else (it["搜索量区间"] or "无量证据")
        lines += [
            "---",
            "",
            "### slug: {}".format(it["slug"]),
            "",
            "- query: {}".format(it["query"]),
            "- 面向角色: {}".format(it["面向角色"] or "-"),
            "- 搜索量: {}".format(vol),
            "- 数据来源: {}".format(it["数据来源"] or "-"),
            "",
        ]
    lines += ["---", "", "现在逐篇输出。"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# assemble:解析 → 落盘 → 台账
# --------------------------------------------------------------------------

def parse_output(text):
    """claude 输出 → ({slug: {evidence, title, body}}, {slug: refuse_reason})"""
    drafts, refused = {}, {}
    pat = re.compile(r"===DRAFT\s+([a-z0-9-]+)===\s*\n(.*?)\n?===END\s+\1===",
                     re.DOTALL)
    for m in pat.finditer(text):
        slug, block = m.group(1), m.group(2).strip()
        ev_m = re.match(r"EVIDENCE:\s*(.+)", block)
        ti_m = re.search(r"^TITLE:\s*(.+)$", block, re.MULTILINE)
        if not ev_m or not ti_m:
            refused[slug] = "包缺 EVIDENCE 或 TITLE 行,按解析失败处理"
            continue
        body = block[ti_m.end():].strip()
        codes = [c.strip() for c in ev_m.group(1).split(",") if c.strip()]
        drafts[slug] = {"evidence": codes, "title": ti_m.group(1).strip(),
                        "body": body}
    rpat = re.compile(r"===REFUSE\s+([a-z0-9-]+)===\s*\n(.*?)\n?===END\s+\1===",
                      re.DOTALL)
    for m in rpat.finditer(text):
        refused[m.group(1)] = m.group(2).strip()
    return drafts, refused


def register_ledger(item, codes):
    """经 j1_evidence.py 登记台账。单一写路径:闸门在那边,这里不复制。"""
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "j1_evidence.py"),
           "--type", "aeo", "--query", item["query"], "--evidence"] + codes + ["--commit"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        return {"ok": False, "rc": proc.returncode,
                "stderr": proc.stderr[-500:], "stdout": proc.stdout[-500:]}
    out = json.loads(proc.stdout)
    return {"ok": True, "ledger_row_id": out.get("ledger_row_id"),
            "gates": out.get("gates")}


def article_file(item, draft, candidates_by_code, now):
    header = [
        "<!--",
        "J1 AEO 内容 · 草稿(台账状态=草稿,等真人签发)",
        "query:        {}".format(item["query"]),
        "证据编号:     {}".format(", ".join(draft["evidence"])),
        "生成:         j1_runner 三段式,claude -p + ai-writing-guideline",
        "日期:         {}".format(now.strftime("%Y-%m-%d")),
        "-->",
        "",
    ]
    return "\n".join(header) + draft["body"] + "\n"


def notify_file(item, draft, ledger, outfile, cfg):
    row_id = (ledger or {}).get("ledger_row_id") or ""
    notion_url = ("https://www.notion.so/" + row_id.replace("-", "")) if row_id else "(未登记)"
    lines = [
        "📮 AEO · J1 内容草稿",
        "",
        "选题:{}".format(item["query"]),
        "标题:{}".format(draft["title"]),
        "证据:{}".format(", ".join(draft["evidence"])),
        "",
        "正文:{}".format(outfile),
        "台账行(去签发):{}".format(notion_url),
        "",
        # 「填签发日期」不是可选项:只改状态的话,站点 lint 会两头堵死
        # (填了 signed_off 报「台账签发日期是空的」,不填报「signed_off 为空」),
        # 两条路都 build fail。2026-08-10 那 4 行就是这么卡住的。
        "签发动作:Notion 里把该行「状态」从 草稿 改为 已签发,**并填「签发日期」**。",
        "两件事都做完才发得出去,只改状态站点会 build fail。",
        "",
        "签发后:python3 scripts/j1_publish.py --list 看待发布清单。",
    ]
    text = "\n".join(lines)
    cap = cfg["telegram"]["max_chars_per_message"]
    return text[:cap]


# --------------------------------------------------------------------------

def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--emit-prompt", dest="emit_prompt", default=None,
                        help="把 claude prompt 写到这个路径(production plan)")
    parser.add_argument("--assemble", default=None,
                        help="读 claude 输出文件,落盘并登记台账")
    parser.add_argument("--plan-file", dest="plan_file", default=None,
                        help="assemble 读取的 plan JSON,默认取当日")
    parser.add_argument("--limit", type=int, default=None, help="临时压低本次篇数")
    args = parser.parse_args()

    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        cfg = ac.load_config("j1.yaml")
        ocfg = ac.load_config("outreach.yaml")

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        now = datetime.now(tz)
        plan_path_default = os.path.join(
            ac.LOGS_DIR, "{}_plan_{}.json".format(SCRIPT, now.strftime("%Y-%m-%d")))

        skill_report = {"missing_required": [], "skills": {}}
        for name, entry in ocfg["skills"]["registry"].items():
            path, _ = sk.resolve_skill(name, entry)
            skill_report["skills"][name] = {"available": bool(path)}
            if not path and entry.get("required"):
                skill_report["missing_required"].append(name)

        # ---- assemble 分支 ----
        if args.assemble:
            plan_path = args.plan_file or plan_path_default
            if not os.path.exists(plan_path):
                raise RuntimeError("找不到 plan 文件:{}(先跑一次 --emit-prompt)".format(plan_path))
            with open(plan_path, encoding="utf-8") as fh:
                plan = json.load(fh)
            with open(args.assemble, encoding="utf-8") as fh:
                drafts, refused = parse_output(fh.read())

            cand_codes = {c["code"] for c in plan["evidence_candidates"]}
            cand_by_code = {c["code"]: c for c in plan["evidence_candidates"]}
            commit = sc.resolve_mode(args) == "commit"

            # 台账新鲜快照,防 assemble 重跑重复登记(plan 时的去重只防选题层面)
            notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
            ledger_now = notion.query_all(env["DS_LEDGER"])
            ledger_facing = {sc.norm_query(ac.rich_text(r.get("properties", {}), "面向"))
                             for r in ledger_now}

            written, failed = [], []
            for item in plan["drafts"]:
                slug = item["slug"]
                if slug in refused:
                    failed.append({"slug": slug, "query": item["query"],
                                   "reason": "claude REFUSE:{}".format(refused[slug])})
                    continue
                d = drafts.get(slug)
                if not d:
                    failed.append({"slug": slug, "query": item["query"],
                                   "reason": "claude 输出里没有这一篇的 DRAFT 包"})
                    continue
                bad = [c for c in d["evidence"] if c not in cand_codes]
                if bad:
                    failed.append({"slug": slug, "query": item["query"],
                                   "reason": "引用了不在候选表里的证据编号:{}".format(
                                       ", ".join(bad))})
                    continue

                fname = "j1_draft_{}_{}.md".format(now.strftime("%Y-%m-%d"), slug)
                fpath = os.path.join(ac.REPO, "outbox", fname)
                ledger_result = None
                if commit:
                    ac.write_outbox(fname, article_file(item, d, cand_by_code, now))
                    if sc.norm_query(item["query"]) in ledger_facing:
                        ledger_result = {"ok": True, "already_registered": True,
                                         "ledger_row_id": None}
                    else:
                        ledger_result = register_ledger(item, d["evidence"])
                        if not ledger_result["ok"]:
                            failed.append({"slug": slug, "query": item["query"],
                                           "reason": "台账登记失败:{}".format(
                                               ledger_result)})
                            continue
                    nname = "j1_notify_{}_{}.md".format(now.strftime("%Y-%m-%d"), slug)
                    ac.write_outbox(nname, notify_file(item, d, ledger_result,
                                                       fpath, cfg))
                written.append({"slug": slug, "query": item["query"],
                                "title": d["title"], "evidence": d["evidence"],
                                "file": fname,
                                "ledger": ledger_result})

            # 独立回读:重新拉台账,逐篇确认「面向」能找到,不信 create 回执
            readback = []
            if commit and written:
                fresh = notion.query_all(env["DS_LEDGER"])
                fresh_facing = {sc.norm_query(ac.rich_text(r.get("properties", {}), "面向"))
                                for r in fresh}
                for w in written:
                    readback.append({
                        "slug": w["slug"],
                        "ledger_readback_ok":
                            sc.norm_query(w["query"]) in fresh_facing})

            result = {
                "script": SCRIPT, "step": "assemble", "mode": sc.resolve_mode(args),
                "generated_at": now.isoformat(),
                "written": written, "failed": failed, "readback": readback,
                "wrote_outbox": commit,
            }
            sc.emit(SCRIPT + "_assemble", result, th)
            bad_readback = [r for r in readback if not r["ledger_readback_ok"]]
            return 1 if bad_readback else 0

        # ---- plan 分支 ----
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        queries = notion.query_all(env["DS_QUERY"])
        ledger = notion.query_all(env["DS_LEDGER"])
        pipeline = notion.query_all(env["DS_PIPELINE"])

        picked, skipped = pick_queries(queries, ledger, cfg)
        if args.limit is not None:
            picked = picked[:args.limit]
        candidates = evidence_candidates(pipeline, cfg)

        result = {
            "script": SCRIPT, "step": "plan", "mode": sc.resolve_mode(args),
            "generated_at": now.isoformat(),
            "row_counts_read": {"query": len(queries), "ledger": len(ledger),
                                "pipeline": len(pipeline)},
            "drafts": picked, "skipped": skipped,
            "evidence_candidates": candidates,
            "skill_report": skill_report,
        }

        if args.emit_prompt:
            # plan 文件只在 production(--emit-prompt)时写,
            # 诊断性 dry-run 不碰它——Phase 3 §七④ 覆写事故的教训。
            with open(plan_path_default, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=2)
            prompt = build_prompt(picked, candidates, cfg, skill_report)
            with open(args.emit_prompt, "w", encoding="utf-8") as fh:
                fh.write(prompt)
            result["plan_file"] = plan_path_default
            result["prompt_file"] = args.emit_prompt

        print("DRAFTS_PLANNED: {}".format(len(picked)), file=sys.stderr)
        sc.emit(SCRIPT, result, th)
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
