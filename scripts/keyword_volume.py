#!/usr/bin/env python3
"""AEO Engine · A1 —— 痛点级 query 的月搜索量入 Query 库。

依据：Build Spec · Phase 2 §一.2 与 §四「脚本要点 · keyword_volume.py」。

两条路径，spec 要求都实现、先用降级路径跑通：
  * 降级路径（默认，--source csv）：真人从 Keyword Planner 手动导出 CSV 放 data/kw/，
    本脚本解析入 Query 库。
  * API 路径（--source api）：Google Ads API。**接口留着，实现未接**——
    前置凭据（developer token + OAuth）当前不在 .env，按纪律报缺凭据退出，
    不降级、不静默跳过。

用法：
    python3 scripts/keyword_volume.py                  # dry-run（默认），解析 data/kw/
    python3 scripts/keyword_volume.py --print-seeds    # 只打印该往 Keyword Planner 里贴的词
    python3 scripts/keyword_volume.py --commit         # 真写 Query 库

输出：JSON 到 stdout，同内容留档 logs/keyword_volume_YYYY-MM-DD.json
"""

import csv
import glob
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "keyword_volume"

# Keyword Planner 导出的列名在不同界面语言/版本下不一致，这里列出认得的几种。
# 认不出就整份文件报错，不猜——猜错的后果是把错的搜索量写进 Query 库。
KEYWORD_COLUMNS = ("keyword", "keyword (by relevance)", "search term", "关键字", "关键词")
VOLUME_COLUMNS = ("avg. monthly searches", "avg. monthly searches (exact match)",
                  "monthly searches", "avg monthly searches", "月均搜索量", "平均每月搜索量")

# Google Ads 凭据的键名（用于报缺凭据时把话说清楚）
GOOGLE_ADS_KEYS = ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID",
                   "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN",
                   "GOOGLE_ADS_CUSTOMER_ID")


# --------------------------------------------------------------------------
# 种子词
# --------------------------------------------------------------------------

def build_seeds(seg_cfg, scan_cfg):
    """首批 query + segments.yaml 衍生词。每条带出处，出处不明的不许进。"""
    q = scan_cfg["query"]
    seeds, seen = [], set()

    for text in q["first_batch"]:
        key = sc.norm_query(text)
        if key in seen:
            continue
        seen.add(key)
        seeds.append({"query 文本": text,
                      "provenance": "Phase 2 spec §一.2 首批 query（逐字）"})

    for seg, body in seg_cfg["segments"].items():
        for text in body.get("linkedin_keywords") or []:
            key = sc.norm_query(text)
            if key in seen:
                continue
            seen.add(key)
            seeds.append({
                "query 文本": text,
                "provenance": "segments.yaml:{}.linkedin_keywords（该文件已审核，"
                              "但其 linkedin_keywords 内容本身无出处——见 Phase 1 §九②）".format(seg),
                "segment": seg,
            })

    for s in seeds:
        s["类型"] = classify_type(s["query 文本"], scan_cfg)
        s["面向角色"] = q["role_by_type"][s["类型"]]
    return seeds


def classify_type(text, scan_cfg):
    """类型判定。规则全部来自 scan.yaml，脚本内不写死任何 marker。

    按**整词**匹配而不是子串：子串匹配会把 "vod review workflow"（痛点侧）
    误判成评估式。（2026-08-04 首次 dry-run 实测踩到，marker 表已同步收窄。）
    """
    markers = scan_cfg["query"]["type_markers"]
    low = sc.norm_query(text)
    for brand in scan_cfg["probe"]["brand_names"]:
        if re.search(r"\b{}\b".format(re.escape(brand.lower())), low):
            return "品牌词"
    for m in markers["评估式"]:
        if re.search(r"\b{}\b".format(re.escape(m.lower().strip())), low):
            return "评估式"
    return markers["default"]


# --------------------------------------------------------------------------
# 降级路径：解析 Keyword Planner 导出的 CSV
# --------------------------------------------------------------------------

def _decode(path):
    """KP 导出常见 UTF-16 带 BOM 的 TSV，也有 UTF-8 CSV。

    UTF-16 只在真的有 BOM 时才用：纯 ASCII 的 UTF-8 文件字节数为偶数时
    用 utf-16 解码**不会报错**，只会解出一串乱码汉字，然后表头行永远找不到。
    （2026-08-04 首次 dry-run 实测踩到，现象是「找不到表头行」而不是解码失败。）
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError("无法解码：无 UTF-16 BOM，且 utf-8-sig / utf-8 / latin-1 均失败")


def _find_header(lines):
    """跳过 KP 导出顶部的说明行，找到真正的表头行。

    判据：该行同时含一个认得的关键词列名和一个认得的搜索量列名。
    找不到就抛错——宁可整份不解析，也不解析出错的对应关系。
    """
    for idx, line in enumerate(lines):
        low = line.lower()
        has_kw = any(c in low for c in KEYWORD_COLUMNS)
        has_vol = any(c in low for c in VOLUME_COLUMNS)
        if has_kw and has_vol:
            delim = "\t" if line.count("\t") >= line.count(",") else ","
            return idx, delim
    raise ValueError(
        "找不到表头行：需要同时含关键词列（{}）与搜索量列（{}）之一".format(
            " / ".join(KEYWORD_COLUMNS[:3]), " / ".join(VOLUME_COLUMNS[:3])))


def _pick(fieldnames, candidates):
    for name in fieldnames or []:
        if (name or "").strip().lower() in candidates:
            return name
    return None


def parse_volume(raw):
    """搜索量取值 → (number 或 None, 说明)。

    KP 在没有在投广告的账号下只给区间（如 "1K – 10K"）。
    区间不折算成任何具体数字：Phase 0 Query 库 §4 写的是「未知留空」，
    折算等于伪造精度。留空并把原始串记进说明。
    """
    s = (raw or "").strip()
    if not s or s in ("-", "—", "–", "0", "N/A"):
        return None, "空值或无数据（原始值：{!r}）".format(raw)
    plain = s.replace(",", "").replace(" ", "")
    if re.fullmatch(r"\d+(\.\d+)?", plain):
        return int(float(plain)), ""
    return None, "非精确值，按「未知留空」处理（原始值：{!r}）".format(s)


def parse_csv_dir(csv_dir):
    """解析目录下所有 CSV/TSV。返回 (rows, files_report)。"""
    paths = sorted(glob.glob(os.path.join(csv_dir, "*.csv")) +
                   glob.glob(os.path.join(csv_dir, "*.tsv")))
    rows, report = [], []
    for path in paths:
        name = os.path.basename(path)
        try:
            text, enc = _decode(path)
            lines = text.splitlines()
            head_idx, delim = _find_header(lines)
            reader = csv.DictReader(io.StringIO("\n".join(lines[head_idx:])),
                                    delimiter=delim)
            kw_col = _pick(reader.fieldnames, KEYWORD_COLUMNS)
            vol_col = _pick(reader.fieldnames, VOLUME_COLUMNS)
            n = 0
            for rec in reader:
                kw = (rec.get(kw_col) or "").strip()
                if not kw:
                    continue
                vol, note = parse_volume(rec.get(vol_col))
                rows.append({"query 文本": kw, "月搜索量": vol,
                             "volume_note": note, "source_file": name})
                n += 1
            report.append({"file": name, "encoding": enc,
                           "delimiter": "TAB" if delim == "\t" else ",",
                           "header_line": head_idx + 1,
                           "keyword_column": kw_col, "volume_column": vol_col,
                           "parsed_rows": n, "error": None})
        except (OSError, ValueError) as exc:
            report.append({"file": name, "parsed_rows": 0, "error": str(exc)})
    return rows, report


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--source", choices=("csv", "api"), default="csv",
                        help="csv=降级路径（默认）；api=Google Ads API（未接，报缺凭据）")
    parser.add_argument("--csv-dir", default=None,
                        help="默认 data/kw/")
    parser.add_argument("--print-seeds", action="store_true",
                        help="只打印种子词清单，供真人贴进 Keyword Planner")
    args = parser.parse_args()
    mode = sc.resolve_mode(args)

    th = ac.load_config("thresholds.yaml")
    try:
        seg_cfg = ac.load_config("segments.yaml")
        scan_cfg = ac.load_config("scan.yaml")
        seeds = build_seeds(seg_cfg, scan_cfg)

        if args.print_seeds:
            sc.emit(SCRIPT, {"script": SCRIPT, "status": "seeds_only",
                             "seed_count": len(seeds), "seeds": seeds,
                             "wrote_notion": False}, th)
            return sc.EXIT_OK

        env = ac.load_env()

        if args.source == "api":
            missing = [k for k in GOOGLE_ADS_KEYS if not env.get(k)]
            sc.missing_credential(
                SCRIPT, missing,
                "Google Ads API 路径的凭据不在 .env（缺 {} 项）。"
                "spec §四 允许先走降级路径：真人从 Keyword Planner 导出 CSV 放 data/kw/，"
                "再跑 --source csv。".format(len(missing)), th,
                extra={"seed_count": len(seeds)})

        csv_dir = args.csv_dir or os.path.join(ac.REPO, "data", "kw")
        os.makedirs(csv_dir, exist_ok=True)
        parsed, files_report = parse_csv_dir(csv_dir)

        # Query 库现状 → 去重
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        existing_rows = notion.query_all(env["DS_QUERY"])
        existing = {}
        for r in existing_rows:
            key = sc.norm_query(ac.title_text(r.get("properties", {}), "query 文本"))
            if key:
                existing[key] = r

        seed_index = {sc.norm_query(s["query 文本"]): s for s in seeds}
        qcfg = scan_cfg["query"]
        ds_value = qcfg["data_source_values"]["keyword_volume"]

        to_create, to_update, skipped = [], [], []
        for row in parsed:
            key = sc.norm_query(row["query 文本"])
            seed = seed_index.get(key)
            typ = seed["类型"] if seed else classify_type(row["query 文本"], scan_cfg)
            item = {
                "query 文本": row["query 文本"],
                "类型": typ,
                "面向角色": qcfg["role_by_type"][typ],
                "月搜索量": row["月搜索量"],
                "数据来源": ds_value,
                "状态": qcfg["initial_status"],
                "volume_note": row["volume_note"],
                "source_file": row["source_file"],
                "in_seed_list": bool(seed),
                "provenance": seed["provenance"] if seed else
                              "Keyword Planner 导出（非种子词，由 KP 扩展建议带出）【推演待校准】",
            }
            if key in existing:
                item["existing_page_id"] = existing[key]["id"]
                # 已有行的来源标注要留着，不能被本脚本盖掉。见下方 commit 分支的注释。
                item["existing_数据来源"] = ac.select_name(
                    existing[key].get("properties", {}), "数据来源")
                if row["月搜索量"] is None:
                    skipped.append(dict(item, skip_reason="已在 Query 库且本次无精确搜索量，不覆盖"))
                else:
                    to_update.append(item)
            else:
                to_create.append(item)

        # 种子词里 CSV 没覆盖到的，如实列出，别假装齐了
        covered = {sc.norm_query(r["query 文本"]) for r in parsed}
        uncovered = [s for s in seeds if sc.norm_query(s["query 文本"]) not in covered]

        written = []
        if mode == "commit":
            for item in to_create:
                page = notion.create_page(env["DS_QUERY"], {
                    "query 文本": sc.p_title(item["query 文本"]),
                    "类型": sc.p_select(item["类型"]),
                    "面向角色": sc.p_select(item["面向角色"]),
                    "月搜索量": sc.p_number(item["月搜索量"]),
                    "数据来源": sc.p_select(item["数据来源"]),
                    "状态": sc.p_select(item["状态"]),
                })
                written.append({"action": "create", "query 文本": item["query 文本"],
                                "page_id": page["id"]})
            for item in to_update:
                # 本脚本补的是**搜索量**，不是来源。已有来源标注一律不覆盖，
                # 只在该字段为空时才写——与 serp_scan.py:184 同一条口径。
                #
                # 为什么这一条重要（2026-08-05 Shawn 拍板修，此前是无条件覆写）：
                # Query 库的 `数据来源` 当天 additive 解冻加了「AI 建议」与「A1 扫描」，
                # 用来区分「模型说会有人搜的词」「真人发帖里抓的词」「买家亲口说的话」。
                # 无条件覆写会在补量的那一瞬把这三者全抹成「Keyword Planner」——
                # 解冻换来的出处区分当场归零，而且是**静默**归零：
                # 补量成功了，数字是对的，出处没了，没有任何报警会响。
                props = {"月搜索量": sc.p_number(item["月搜索量"])}
                if not item.get("existing_数据来源"):
                    props["数据来源"] = sc.p_select(item["数据来源"])
                notion.update_page(item["existing_page_id"], props)
                written.append({"action": "update", "query 文本": item["query 文本"],
                                "page_id": item["existing_page_id"],
                                "数据来源": item.get("existing_数据来源") or item["数据来源"],
                                "数据来源_是否本次写入": "数据来源" in props})

        sc.emit(SCRIPT, {
            "script": SCRIPT,
            "mode": mode,
            "status": "ok",
            "wrote_notion": mode == "commit",
            "source": args.source,
            "csv_dir": os.path.relpath(csv_dir, ac.REPO),
            "files": files_report,
            "counts": {
                "seeds": len(seeds),
                "parsed_rows": len(parsed),
                "query_db_existing": len(existing_rows),
                "to_create": len(to_create),
                "to_update": len(to_update),
                "skipped": len(skipped),
                "seeds_not_covered_by_csv": len(uncovered),
            },
            "to_create": to_create,
            "to_update": to_update,
            "skipped": skipped,
            "seeds_not_covered_by_csv": uncovered,
            "written": written,
            "notes": [
                "月搜索量为 null 表示 CSV 给的是区间或空值。Phase 0 Query 库 §4 口径是"
                "「未知留空」，不折算区间中值——折算等于伪造精度。",
                "类型 / 面向角色 的判定规则来自 config/scan.yaml query 节，整节【推演待校准】。",
                "更新已有行时只补「月搜索量」；「数据来源」仅在为空时才写，不覆盖已有标注"
                "（2026-08-05 Shawn 拍板改。此前无条件覆写，会把「AI 建议」「A1 扫描」"
                "「买家原话」三种出处静默抹成「Keyword Planner」）。",
            ],
        }, th)
        return sc.EXIT_OK

    except Exception as exc:  # noqa: BLE001 —— 与 Phase 1 同：宁可整体失败，不产出半份结果
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
