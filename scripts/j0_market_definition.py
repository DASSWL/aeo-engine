#!/usr/bin/env python3
"""AEO Engine · J0 —— 市场定义（骨架）。

依据：Build Spec · Phase 3「J0 市场定义」与「§J0 运行流程」。

触发：win/loss 库每新增 5 条，A7 触发。
流程：读 win/loss 全库 →「现在怎么解决 / 评估过什么工具」列做提及计数 →
      与现行竞替名单 diff → 输出新增候选、提及频次、证据行号 → 写「J0 输出」子页
      加 Telegram 摘要。采纳与否真人定，采纳后同步 Mega Doc 并记 Changelog。

约束（spec §J0）：
  * 每条结论标证据来源（win/loss 行）
  * **禁止无证据新增竞品**
  * 冷启动的 5 个定义永远标注拍的假设

当前状态：**无燃料**。win/loss 库 0 行，够不到 5 条触发线。
骨架此刻的正确行为是拒绝并说清还差几条，不是拿零条对话去"推演"一份竞替名单——
那正是 spec 明文禁止的「无证据新增竞品」。

用法：
    python3 scripts/j0_market_definition.py
    python3 scripts/j0_market_definition.py --force   # 无视触发线（仍不会无证据新增竞品）
退出码：0 跑完；4 未达触发线被拒；1 执行失败
"""

import os
import re
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "j0_market_definition"
EXIT_REFUSED = 4

SOURCE_FIELD = "现在怎么解决 / 评估过什么工具"   # Phase 0 冻结字段名，含空格斜杠，逐字
STATE_FILE = "j0_last_run.json"


def mention_counts(winloss):
    """对 SOURCE_FIELD 做提及计数，每条计数都带回证据行号。

    刻意**不做模糊归并**（把 "Twelve Labs" 与 "twelvelabs" 合成一个）：
    归并规则本身就是一个判断，而这个脚本的产出是要拿去改竞替名单的。
    归并错了，错误会以「数据」的面目进入名单。分开列，由真人合。
    """
    counts, evidence = Counter(), {}
    for row in winloss:
        p = row.get("properties", {})
        text = ac.rich_text(p, SOURCE_FIELD)
        if not text.strip():
            continue
        title = ac.title_text(p, "对话标识")
        # 按常见分隔符切成候选工具名，不做语义理解
        for token in re.split(r"[,，;；/、\n]+", text):
            name = token.strip(" 。.·-").strip()
            if len(name) < 2:
                continue
            counts[name] += 1
            evidence.setdefault(name, []).append({
                "对话标识": title, "row_id": row["id"], "url": row.get("url")})
    return counts, evidence


def current_competitors(cfg_scan):
    """现行竞替名单。当前唯一有出处的来源是 Phase 2 的探测问题（scan.yaml）。"""
    names = set()
    probe = (cfg_scan.get("probe") or {})
    for key in ("competitors", "competitor_names"):
        for n in (probe.get(key) or []):
            names.add(str(n).strip())
    # 探测问题文本里点过名的竞品（Phase 2 裁决① 给的两个）
    for q in (probe.get("questions") or []):
        text = q if isinstance(q, str) else (q.get("query") or q.get("text") or "")
        for known in ("twelve labs", "twelvelabs", "chatcut"):
            if known in str(text).lower():
                names.add(known)
    return sorted(names)


def output_page_template(new_candidates, counts, evidence, winloss_count, now):
    """spec §J0「写专用输出页」的模板。骨架阶段先把模板定死，有燃料时直接填。"""
    lines = [
        "# J0 输出 · 市场定义校准 {}".format(now.strftime("%Y-%m-%d")),
        "",
        "触发：win/loss 库达到 {} 条。".format(winloss_count),
        "输入：win/loss 全库「{}」列。".format(SOURCE_FIELD),
        "",
        "## 一、竞替名单新增候选",
        "",
        "| 候选 | 提及频次 | 证据行 |",
        "|---|---|---|",
    ]
    if not new_candidates:
        lines.append("| （无新增候选） | — | — |")
    for name in new_candidates:
        rows = "、".join(e["对话标识"] or e["row_id"][:8] for e in evidence.get(name, []))
        lines.append("| {} | {} | {} |".format(name, counts[name], rows or "—"))
    lines += [
        "",
        "**禁止无证据新增竞品**：上表每一行的「证据行」都必须非空。空的一律不许进名单。",
        "",
        "## 二、segment 定义修订建议",
        "",
        "（按 win/loss 实际分布填。冷启动的 5 个 segment 定义**永远标注为拍的假设**，",
        "在被真实对话推翻之前不许写成结论。）",
        "",
        "## 三、类目锚点语言候选",
        "",
        "（取买家原话里反复出现的说法。每条标出处 win/loss 行。）",
        "",
        "## 四、采纳与否",
        "",
        "真人定。采纳后同步 Mega Doc 并记 Changelog——本脚本不碰这两处。",
    ]
    return "\n".join(lines)


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="无视触发线强跑（仍不会无证据新增竞品）")
    args = parser.parse_args()

    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        gates = ac.load_config("gates.yaml")
        cfg_scan = ac.load_config("scan.yaml")

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        now = datetime.now(tz)

        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        winloss = notion.query_all(env["DS_WINLOSS"])

        # 触发线：spec「win/loss 库每新增 5 条」。批次大小复用 gates 的 win_loss_min，
        # 不在脚本里另写一个 5——两个 5 迟早会分家。
        batch = gates["neighborhood"]["win_loss_min"]
        state_path = os.path.join(ac.DATA_DIR, STATE_FILE)
        last_count = 0
        if os.path.exists(state_path):
            import json
            with open(state_path, encoding="utf-8") as fh:
                last_count = json.load(fh).get("winloss_count_at_last_run", 0)
        delta = len(winloss) - last_count

        result = {
            "script": SCRIPT, "mode": sc.resolve_mode(args),
            "generated_at": now.isoformat(),
            "trigger": {"batch_size": batch, "batch_size_source": "gates.yaml:neighborhood.win_loss_min",
                        "winloss_count": len(winloss),
                        "winloss_count_at_last_run": last_count,
                        "new_since_last_run": delta},
            "wrote_notion": False,
        }

        if delta < batch and not args.force:
            result["refused"] = True
            result["refused_at"] = "触发线"
            result["reasons"] = [
                "win/loss 库自上次运行新增 {} 条，未达触发线 {} 条。".format(delta, batch),
                "当前全库 {} 行。".format(len(winloss)),
                "还差 {} 条对话。".format(max(0, batch - delta)),
                "不强跑的理由：J0 的产出是竞替名单与 segment 定义修订建议，"
                "样本不足时跑出来的是推演不是证据，而 spec 明文禁止无证据新增竞品。",
                "要看模板长什么样可以加 --force，但它同样不会凭空造出竞品名。",
            ]
            result["what_is_needed"] = "补 {} 条 win/loss 记录（含「{}」列）".format(
                max(0, batch - delta), SOURCE_FIELD)
            sc.emit(SCRIPT, result, th)
            sys.exit(EXIT_REFUSED)

        counts, evidence = mention_counts(winloss)
        known = {n.lower() for n in current_competitors(cfg_scan)}
        new_candidates = [n for n in counts if n.lower() not in known]
        # 禁止无证据新增：没有证据行的候选直接剔除
        new_candidates = [n for n in new_candidates if evidence.get(n)]
        new_candidates.sort(key=lambda n: -counts[n])

        result["refused"] = False
        result["current_competitor_list"] = sorted(known)
        result["mention_counts"] = dict(counts.most_common())
        result["new_candidates"] = [
            {"name": n, "count": counts[n], "evidence": evidence[n]} for n in new_candidates]
        result["cold_start_note"] = (
            "冷启动的 5 个 segment 定义永远标注为拍的假设，"
            "在被真实对话推翻之前不许写成结论。")

        page = output_page_template(new_candidates, counts, evidence, len(winloss), now)
        if sc.resolve_mode(args) == "commit":
            path = ac.write_outbox("j0_output_{}.md".format(now.strftime("%Y-%m-%d")), page)
            result["output_page"] = path
            import json
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump({"winloss_count_at_last_run": len(winloss),
                           "ran_at": now.isoformat()}, fh, ensure_ascii=False, indent=2)
            print("PUSH: {}".format(path))
        else:
            result["output_page_preview"] = page

        sc.emit(SCRIPT, result, th)
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
