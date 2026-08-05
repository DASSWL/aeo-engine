#!/usr/bin/env python3
"""AEO Engine · A1 —— 把定稿的探测问题灌进 Query 库。

依据：Build Spec · Phase 2 §一.3「探测问题清单 v1（两套，首轮真人过目后入 Query 库）」。

为什么需要这个脚本而不是手工建行：
  探测问题入库是**五库的第一次真实写入**。Phase 0 那六条测试记录清理折腾过一轮，
  之后立的规矩是「写库必须显式 --commit、必须先能 dry-run 看一遍」。
  手工在 Notion 界面里敲 10 行，既绕过了这条规矩，也没法复核字段取值对不对。

源头：config/scan.yaml 的 probe.questions（机器可读的唯一来源）。
      prompts/probe_questions_v1.md 是审核记录与理由，不参与运行。

用法：
    python3 scripts/probe_questions_sync.py            # dry-run（默认），只打印计划
    python3 scripts/probe_questions_sync.py --commit   # 真写 Query 库

幂等：按 `query 文本` 归一化去重，已存在的跳过，不重复建行、不覆盖已有行。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "probe_questions_sync"


def build_plan(scan_cfg):
    """展开两套问题 → 待写入 Query 库的行。字段名逐字取自 Phase 0 核对清单。"""
    probe = scan_cfg["probe"]
    qcfg = scan_cfg["query"]
    field_map = probe["question_set_query_fields"]
    ds_value = qcfg["data_source_values"]["probe"]

    rows, problems = [], []
    for set_name in probe["question_sets"]:
        items = (probe.get("questions") or {}).get(set_name)
        if not items:
            problems.append("问题集 {!r} 在 scan.yaml probe.questions 下没有内容".format(set_name))
            continue
        if len(items) != probe["questions_per_set"]:
            # 不是致命错，但必须说出来：spec 的口径是「两套各 5 条起步」
            problems.append(
                "问题集 {!r} 有 {} 条，与 questions_per_set={} 不一致".format(
                    set_name, len(items), probe["questions_per_set"]))
        fields = field_map[set_name]
        for item in items:
            rows.append({
                "query 文本": item["text"],
                "类型": fields["类型"],
                "面向角色": fields["面向角色"],
                "月搜索量": None,        # Phase 0 Query 库 §4：未知留空
                "数据来源": ds_value,     # 「探测问题」
                "状态": qcfg["initial_status"],   # 「候选」
                "问题集": set_name,       # 仅供复核，Query 库没有这个字段，不写入
                "provenance": " ".join(item["provenance"].split()),
            })
    return rows, problems


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    args = parser.parse_args()
    mode = sc.resolve_mode(args)

    th = ac.load_config("thresholds.yaml")
    try:
        scan_cfg = ac.load_config("scan.yaml")
        if scan_cfg["meta"]["status"] != "approved":
            raise RuntimeError(
                "scan.yaml 的 meta.status 是 {!r}，不是 approved。"
                "未经审核的问题不许入库——spec §一.3：首轮真人过目后才入 Query 库。".format(
                    scan_cfg["meta"]["status"]))

        rows, problems = build_plan(scan_cfg)
        env = ac.load_env()
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        existing_rows = notion.query_all(env["DS_QUERY"])
        existing = sc.existing_query_texts(existing_rows)

        to_create, skipped = [], []
        for row in rows:
            if sc.norm_query(row["query 文本"]) in existing:
                skipped.append(dict(row, skip_reason="Query 库已有同名 query，跳过不覆盖"))
            else:
                to_create.append(row)
                existing.add(sc.norm_query(row["query 文本"]))

        written = []
        if mode == "commit":
            for row in to_create:
                page = notion.create_page(env["DS_QUERY"], {
                    "query 文本": sc.p_title(row["query 文本"]),
                    "类型": sc.p_select(row["类型"]),
                    "面向角色": sc.p_select(row["面向角色"]),
                    "月搜索量": sc.p_number(row["月搜索量"]),
                    "数据来源": sc.p_select(row["数据来源"]),
                    "状态": sc.p_select(row["状态"]),
                })
                written.append({"action": "create", "query 文本": row["query 文本"],
                                "page_id": page["id"]})

        sc.emit(SCRIPT, {
            "script": SCRIPT,
            "mode": mode,
            "status": "ok",
            "wrote_notion": mode == "commit",
            "scan_yaml_status": scan_cfg["meta"]["status"],
            "counts": {
                "planned": len(rows),
                "query_db_existing": len(existing_rows),
                "to_create": len(to_create),
                "skipped_existing": len(skipped),
            },
            "config_problems": problems,
            "to_create": to_create,
            "skipped_existing": skipped,
            "written": written,
            "notes": [
                "「问题集」一列只是复核用，Query 库没有这个字段，不会写进 Notion。"
                "问题集在 Query 库里靠「面向角色」区分（pain feeler / decision maker）。",
                "「月搜索量」留空是刻意的：探测问题不是从 Keyword Planner 来的，"
                "搜索量未知。Phase 0 Query 库 §4 的口径是未知留空。",
            ],
        }, th)
        return sc.EXIT_OK

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
