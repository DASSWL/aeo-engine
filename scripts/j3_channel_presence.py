#!/usr/bin/env python3
"""AEO Engine · J3 —— 渠道存在感（骨架）。

依据：Build Spec · Phase 3「J3 渠道存在感」与「§J3 补充」。

本阶段只做**纯痛点求解法帖**的诚实回答草稿：
  生成 → Telegram → 真人以本人名义发。

草稿硬要求（spec §J3 补充）：
  * 表明 founder 身份
  * **只引用台账或 benchmark 里可验证的事实**
  * 不贬低竞品
  * 附原帖链接与帖型标注

锁死的两件事（spec §J3）：
  * 工具求推荐帖与 G2 / Capterra 挂靠 —— 锁邻域闸门，gates.yaml 未开时任何请求拒绝
  * YouTube 佐证 —— 本阶段只收集机会清单，不产出

当前状态：**无燃料**。台账 0 行、facts.json 的 benchmark 字段全部「待真人补」，
可引用事实集合为空 → 任何回答草稿请求都会被拒。
这正是骨架该有的行为：没有可验证事实时写出来的"诚实回答"只能靠编。

用法：
    python3 scripts/j3_channel_presence.py --post-url <链接> --post-type pain_point
    python3 scripts/j3_channel_presence.py --post-url <链接> --post-type tool_rec  # 必被闸门拒
退出码：0 通过；4 被拒；1 执行失败
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "j3_channel_presence"
EXIT_REFUSED = 4

# spec §J3：帖型。pain_point 本阶段做；其余锁闸门或不产出。
POST_TYPES = {
    "pain_point": "纯痛点求解法帖（A1 已标注）",
    "tool_rec": "工具求推荐帖（锁邻域闸门）",
    "g2_capterra": "G2 / Capterra 挂靠（锁邻域闸门）",
    "youtube": "YouTube 佐证（本阶段只收集机会清单，不产出）",
}
GATED_TYPES = ("tool_rec", "g2_capterra")

# 站点事实层。J2 把它落在 vivu.ai 仓库，J3 只读不写。
FACTS_PATHS = [
    "/Users/shiyuanniu/project/vivu_web/data/facts.json",
]


def load_facts():
    for p in FACTS_PATHS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return json.load(fh), p
    return None, None


def usable_facts(facts):
    """可引用事实 = facts.json 里 status 已确认且有值的条目。

    「待真人补」的一律不算——那正是 J2 立 facts.json 的意义：
    基准掺假，整条守护链就在守护假话。
    """
    usable = []

    def walk(node, path):
        if node is None or not isinstance(node, (dict, list)):
            return
        if isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "{}[{}]".format(path, i))
            return
        if node.get("status") == "已确认":
            value = node.get("value", node.get("term"))
            if value not in (None, "") and node.get("source"):
                usable.append({"path": path, "value": value, "source": node["source"]})
        for k, v in node.items():
            if k.startswith("_"):
                continue
            walk(v, "{}.{}".format(path, k) if path else k)

    walk(facts, "")
    return usable


def ledger_facts(ledger):
    """台账里可引用的已发布资产（benchmark / case study 等）。"""
    out = []
    for row in ledger:
        p = row.get("properties", {})
        status = ac.select_name(p, "状态")
        if status not in ("已签发", "已发布"):
            continue
        out.append({"资产名": ac.title_text(p, "资产名"),
                    "类型": ac.select_name(p, "类型"),
                    "证据链接": (p.get("证据链接") or {}).get("url"),
                    "发布链接": (p.get("发布链接") or {}).get("url"),
                    "row_id": row["id"]})
    return out


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--post-url", dest="post_url", required=True, help="原帖链接")
    parser.add_argument("--post-type", dest="post_type", required=True,
                        choices=sorted(POST_TYPES), help="帖型")
    parser.add_argument("--post-text", dest="post_text", default="", help="原帖正文")
    args = parser.parse_args()

    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        gates = ac.load_config("gates.yaml")

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        now = datetime.now(tz)

        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        ledger = notion.query_all(env["DS_LEDGER"])
        winloss = notion.query_all(env["DS_WINLOSS"])

        facts, facts_path = load_facts()
        result = {
            "script": SCRIPT, "mode": sc.resolve_mode(args),
            "generated_at": now.isoformat(),
            "request": {"post_url": args.post_url, "post_type": args.post_type,
                        "post_type_label": POST_TYPES[args.post_type]},
            "row_counts_read": {"ledger": len(ledger), "winloss": len(winloss)},
            "facts_source": facts_path,
            "wrote_notion": False,
        }

        def refuse(where, reasons, extra=None):
            result["refused"] = True
            result["refused_at"] = where
            result["reasons"] = reasons
            if extra:
                result.update(extra)
            sc.emit(SCRIPT, result, th)
            sys.exit(EXIT_REFUSED)

        # ---- 闸门①：帖型锁 ----
        if args.post_type == "youtube":
            refuse("帖型", [
                "YouTube 佐证在本阶段只收集机会清单，不产出草稿（spec §J3）。",
                "把机会记进台账即可，本脚本不生成内容。",
            ])
        if args.post_type in GATED_TYPES:
            n = gates["neighborhood"]
            reasons = []
            if len(winloss) < n["win_loss_min"]:
                reasons.append("win/loss 场次 {} < win_loss_min {}".format(
                    len(winloss), n["win_loss_min"]))
            if not n["competitor_list_converged"]:
                reasons.append("competitor_list_converged = false")
            if reasons:
                refuse("② 邻域闸门", [
                    "「{}」锁邻域闸门，闸门未开，任何请求拒绝（spec §J3）。".format(
                        POST_TYPES[args.post_type]),
                ] + reasons, {"neighborhood_detail": dict(n)})

        # ---- 闸门②：可引用事实 ----
        if facts is None:
            refuse("事实来源", [
                "找不到 data/facts.json（查过：{}）。".format("、".join(FACTS_PATHS)),
                "J3 的回答只能引用台账或 benchmark 里可验证的事实，事实层读不到就没法生成。",
                "最可能的原因：facts.json 由 J2 落在 vivu.ai 仓库，"
                "当前还在未合并的 PR 分支 aeo/j2-content-contract 上，"
                "而本机 vivu_web 的工作树 checkout 在别的分支。",
                "补法：合并那个 PR，或先把 vivu_web checkout 到该分支。",
            ])
        usable = usable_facts(facts)
        pub = ledger_facts(ledger)
        result["usable_facts_count"] = len(usable)
        result["ledger_publishable_count"] = len(pub)

        # benchmark 专项：spec 明文「只引用台账或 benchmark 里可验证的事实」
        bench = [u for u in usable if u["path"].startswith("benchmarks")]
        result["benchmark_facts_count"] = len(bench)

        if not pub and not bench:
            refuse("事实来源", [
                "台账里 0 条已签发/已发布资产，facts.json 里 0 条已确认的 benchmark。",
                "spec §J3 补充：回答只引用台账或 benchmark 里**可验证**的事实。",
                "两个来源都空 = 没有任何可引用的东西。此时生成的「诚实回答」只能靠编，"
                "而编造正是这条约束要防的。",
                "补法（二选一）：",
                "  a) 台账登记并签发至少一条资产（benchmark / case study）",
                "  b) 在 vivu.ai 的 data/facts.json 里把某个 benchmark 字段从「待真人补」"
                "改成「已确认」并补上出处",
            ], {
                "facts_available_but_not_benchmark": [
                    {"path": u["path"], "value": u["value"]} for u in usable[:8]],
                "note": "上面这些是 facts.json 里已确认的**产品口径**事实，"
                        "不是 benchmark。spec 把 J3 的引用面限定在台账与 benchmark，"
                        "所以它们不足以解锁生成。",
            })

        # ---- 通过：出草稿骨架 ----
        result["refused"] = False
        result["draft_skeleton"] = {
            "founder_disclosure_required": True,
            "founder_line": "Disclosure: I'm the founder of Vivu.",
            "post_url": args.post_url,
            "post_type_label": POST_TYPES[args.post_type],
            "citable_facts": bench + pub,
            "hard_rules": [
                "表明 founder 身份，放在开头，不藏在末尾",
                "只引用上面 citable_facts 里的事实，一条都不许超出",
                "不贬低竞品",
                "附原帖链接与帖型标注",
            ],
            "generation": "闸门已过。正文由 run_j3.sh 调 claude -p 加载 "
                          "ai-writing-guideline 生成，接口同 J4 的 draft_runner。",
        }
        result["post_send_note"] = (
            "发出后由真人在台账登记（类型：社区回答，附链接），纳入一致性检查覆盖面。")
        sc.emit(SCRIPT, result, th)
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
