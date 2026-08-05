#!/usr/bin/env python3
"""AEO Engine · J1 —— 证据生产（骨架）。

依据：Build Spec · Phase 3「J1 证据生产」。

硬校验顺序逐条照 spec §J1 实现，**顺序不许调换**：
  ① 证据编号存在且可解析（win/loss 行、水箱行或信号链接），缺失即拒绝并说明缺什么
  ② 类型为对比页时查 gates.yaml 邻域闸门，未开即拒绝
  ③ AEO 内容按 Query 库排序（有量优先）
  ④ 产出即登记台账（状态草稿）
  ⑤ 对外口径等真人签发

当前状态：**无燃料**。win/loss 库为空 → 任何带 WL- 证据的请求都解析不了；
台账为空；邻域闸门 competitor_list_converged=false → 对比页一律拒绝。
骨架此刻的正确行为是**拒绝并说清缺什么**，不是产出没有证据的内容。

生成环节接 skill 产线（vivu-outreach 的写作纪律 + ai-writing-guideline），
但只有前四道闸全过了才会走到那一步——闸门不过就没有生成这回事。

用法：
    python3 scripts/j1_evidence.py --type aeo --evidence WL-2026-08-12-Acme --query "..."
    python3 scripts/j1_evidence.py --type compare --evidence SIG-a1b2c3d4 --query "..."
    python3 scripts/j1_evidence.py --type aeo --query "..."        # 无证据 → 必须被拒
退出码：0 通过全部闸门；4 被闸门拒绝；2 缺凭据；1 执行失败
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402
import skill_check as sk     # noqa: E402

SCRIPT = "j1_evidence"
EXIT_REFUSED = 4

# spec §J1「五类内容」→ 台账「类型」选项（Phase 0 冻结，逐字）
LEDGER_TYPE = {
    "aeo": "AEO 内容",
    "compare": "对比页",
    "case_study": "case study",
    "demo_queries": "live demo query 集",
    "linkedin": "LinkedIn 帖",
}
WL_RE = re.compile(r"^WL-(\d{4}-\d{2}-\d{2})-(.+)$")
SIG_RE = re.compile(r"^SIG-([0-9a-zA-Z]+)$")


def resolve_evidence(codes, winloss, pipeline):
    """闸门①：逐条把证据编号解析到五库里的真实行。

    解析不了就逐条列出缺什么——「缺什么」比「拒绝」有用得多，
    因为真人看到缺口才知道下一步该去补哪一行。
    """
    wl_by_key, wl_titles = {}, []
    for row in winloss:
        p = row.get("properties", {})
        title = ac.title_text(p, "对话标识")
        wl_titles.append(title)
        company = ac.rich_text(p, "公司")
        date = p.get("日期", {}).get("date", {}) or {}
        start = (date.get("start") or "")[:10]
        if start and company:
            wl_by_key["{}|{}".format(start, company.strip().lower())] = row

    sig_by_hash = {}
    for row in pipeline:
        p = row.get("properties", {})
        url = (p.get("来源链接") or {}).get("url") or ""
        if url:
            # 与 J2 frontmatter 契约同一口径：SIG- 后面接来源链接的哈希
            import hashlib
            h = hashlib.sha1(sc.norm_url(url).encode("utf-8")).hexdigest()[:8]
            sig_by_hash[h] = row

    resolved, missing = [], []
    for code in codes:
        m = WL_RE.match(code)
        if m:
            key = "{}|{}".format(m.group(1), m.group(2).strip().lower())
            row = wl_by_key.get(key)
            if row:
                resolved.append({"code": code, "kind": "winloss", "row_id": row["id"],
                                 "url": row.get("url")})
            else:
                missing.append({
                    "code": code, "kind": "winloss",
                    "why": "win/loss 库里没有 日期={} 且 公司={} 的行".format(
                        m.group(1), m.group(2)),
                    "winloss_rows_available": len(winloss),
                })
            continue
        m = SIG_RE.match(code)
        if m:
            row = sig_by_hash.get(m.group(1))
            if row:
                resolved.append({"code": code, "kind": "pipeline", "row_id": row["id"],
                                 "url": row.get("url")})
            else:
                missing.append({"code": code, "kind": "pipeline",
                                "why": "水箱里没有来源链接哈希为 {} 的行".format(m.group(1)),
                                "pipeline_rows_available": len(pipeline)})
            continue
        missing.append({"code": code, "kind": "unknown",
                        "why": "编号格式不合法。只认 WL-日期-公司 或 SIG-链接哈希"})
    return resolved, missing


def neighborhood_gate(gates, winloss_count):
    """闸门②：邻域闸门。三个条件同时满足才算开。

    禁止脚本自动翻转 competitor_list_converged——收敛与否是判断，不是计算。
    """
    n = gates["neighborhood"]
    reasons = []
    if winloss_count < n["win_loss_min"]:
        reasons.append("win/loss 场次 {} < win_loss_min {}".format(
            winloss_count, n["win_loss_min"]))
    if not n["competitor_list_converged"]:
        reasons.append("competitor_list_converged = false（竞替名单尚未从真实对话收敛）")
    return (len(reasons) == 0), reasons, {
        "win_loss_min": n["win_loss_min"], "win_loss_target": n["win_loss_target"],
        "competitor_list_converged": n["competitor_list_converged"],
        "winloss_count": winloss_count,
    }


def query_priority(queries, want_query):
    """闸门③：AEO 内容按 Query 库排序，有量优先。

    返回 (匹配到的 Query 行, 全库排序后的前几条)。匹配不到不算拒绝理由，
    但会在输出里说明——spec 把提问量降级成了排序依据，不是开工闸门（gates.yaml 注释）。
    """
    ranked = []
    for row in queries:
        p = row.get("properties", {})
        text = ac.title_text(p, "query 文本")
        vol = (p.get("月搜索量") or {}).get("number")
        ranked.append({"query": text, "月搜索量": vol,
                       "状态": ac.select_name(p, "状态"),
                       "面向角色": ac.select_name(p, "面向角色")})
    # 有量的排前面，同为 None 时保持原序
    ranked.sort(key=lambda r: (r["月搜索量"] is None, -(r["月搜索量"] or 0)))
    match = next((r for r in ranked
                  if want_query and sc.norm_query(r["query"]) == sc.norm_query(want_query)),
                 None)
    return match, ranked[:10]


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--type", required=True, choices=sorted(LEDGER_TYPE),
                        help="内容类型（spec §J1 五类内容）")
    parser.add_argument("--evidence", nargs="*", default=[],
                        help="证据编号，可给多个。不给即视为无证据请求")
    parser.add_argument("--query", default=None, help="面向的搜索问题")
    parser.add_argument("--anchor-terms", nargs="*", default=[], dest="anchor_terms")
    args = parser.parse_args()

    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        gates = ac.load_config("gates.yaml")
        cfg = ac.load_config("outreach.yaml")

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        now = datetime.now(tz)

        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        winloss = notion.query_all(env["DS_WINLOSS"])
        pipeline = notion.query_all(env["DS_PIPELINE"])
        ledger = notion.query_all(env["DS_LEDGER"])
        queries = notion.query_all(env["DS_QUERY"])

        result = {
            "script": SCRIPT, "mode": sc.resolve_mode(args),
            "generated_at": now.isoformat(),
            "request": {"type": args.type, "ledger_type": LEDGER_TYPE[args.type],
                        "evidence": args.evidence, "query": args.query,
                        "anchor_terms": args.anchor_terms},
            "row_counts_read": {"winloss": len(winloss), "pipeline": len(pipeline),
                                "ledger": len(ledger), "query": len(queries)},
            "gates": {},
            "wrote_notion": False,
        }

        def refuse(gate, reasons, missing=None):
            result["refused"] = True
            result["refused_at_gate"] = gate
            result["reasons"] = reasons
            if missing is not None:
                result["missing"] = missing
            result["what_is_needed"] = reasons
            sc.emit(SCRIPT, result, th)
            sys.exit(EXIT_REFUSED)

        # ---- 闸门① 证据 ----
        if not args.evidence:
            result["gates"]["1_evidence"] = "refused"
            refuse("① 证据编号", [
                "本次请求没有给任何证据编号。",
                "spec §全局硬约束：每条对外内容挂证据编号。无证据不生成，这不是可放宽项。",
                "补法：给 --evidence WL-日期-公司（win/loss 行）或 SIG-链接哈希（水箱信号行）。",
                "当前 win/loss 库 {} 行、水箱 {} 行。".format(len(winloss), len(pipeline)),
            ])
        resolved, missing = resolve_evidence(args.evidence, winloss, pipeline)
        if missing:
            result["gates"]["1_evidence"] = "refused"
            result["resolved_evidence"] = resolved
            refuse("① 证据编号", [
                "{} 个证据编号在五库里解析不到。".format(len(missing)),
                "解析失败即拒绝——引用一个不存在的证据，比没有证据更糟。",
            ], missing)
        result["gates"]["1_evidence"] = "passed"
        result["resolved_evidence"] = resolved

        # ---- 闸门② 邻域闸门（仅对比页）----
        if args.type == "compare":
            ok, reasons, detail = neighborhood_gate(gates, len(winloss))
            result["gates"]["2_neighborhood"] = "passed" if ok else "refused"
            result["neighborhood_detail"] = detail
            if not ok:
                refuse("② 邻域闸门", [
                    "对比页请求，但邻域闸门未开：",
                ] + reasons + [
                    "闸门只存在于 config/gates.yaml，脚本不得自动翻转。"
                    "competitor_list_converged 由真人在 J0 重跑校准后手动置位。",
                ])
        else:
            result["gates"]["2_neighborhood"] = "n/a（非对比页）"

        # ---- 闸门③ Query 库排序 ----
        match, top = query_priority(queries, args.query)
        result["gates"]["3_query_priority"] = "passed"
        result["query_match"] = match
        result["query_top10"] = top
        if args.type == "aeo" and not match:
            result["query_note"] = (
                "给的 query 不在 Query 库里。按 gates.yaml 2026-08-04 裁决，"
                "提问量检验是排序依据不是开工闸门，故不拒绝；但建议先把它入 Query 库，"
                "否则排不进生产顺序。")

        # ---- 闸门④ 台账登记（状态草稿）----
        # 骨架阶段：只算出要写什么，--commit 才真写。
        ledger_props = {
            "资产名": sc.p_title("{}｜{}".format(LEDGER_TYPE[args.type],
                                                 args.query or "(未给 query)")),
            "类型": sc.p_select(LEDGER_TYPE[args.type]),
            "面向": sc.p_text(args.query or ""),
            "证据编号": sc.p_relation([r["row_id"] for r in resolved
                                       if r["kind"] == "winloss"]),
            "状态": sc.p_select("草稿"),
            "创建日期": sc.p_date(now.strftime("%Y-%m-%d")),
        }
        result["gates"]["4_ledger"] = "planned"
        result["ledger_row_planned"] = {
            "类型": LEDGER_TYPE[args.type], "状态": "草稿",
            "证据编号_relations": len([r for r in resolved if r["kind"] == "winloss"]),
        }
        if sc.resolve_mode(args) == "commit":
            page = notion.create_page(env["DS_LEDGER"], ledger_props)
            result["wrote_notion"] = True
            result["gates"]["4_ledger"] = "registered"
            result["ledger_row_id"] = page.get("id")

        # ---- 闸门⑤ 真人签发 ----
        result["gates"]["5_signoff"] = "pending_human"
        result["signoff_note"] = (
            "对外口径等真人签发。台账状态停在「草稿」，"
            "J2 的 CI lint 会拒绝任何 signed_off 为空的页上线。")

        # ---- 生成环节（skill 产线接口）----
        skills = {}
        for name, entry in cfg["skills"]["registry"].items():
            path, _ = sk.resolve_skill(name, entry)
            skills[name] = {"available": bool(path)}
        result["generation"] = {
            "status": "not_run",
            "why": "骨架阶段只跑闸门。闸门全过之后由 run_j1.sh 调 claude -p "
                   "加载真 skill 生成正文，接口同 J4 的 draft_runner。",
            "skills": skills,
        }
        result["refused"] = False
        sc.emit(SCRIPT, result, th)
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
