#!/usr/bin/env python3
"""AEO Engine · A1 —— 痛点级 query 的月搜索量入 Query 库。

依据：Build Spec · Phase 2 §一.2 与 §四「脚本要点 · keyword_volume.py」。

两条路径，spec 要求都实现、先用降级路径跑通：
  * 降级路径（默认，--source csv）：真人从 Keyword Planner 手动导出 CSV 放 data/kw/，
    本脚本解析入 Query 库。
  * API 路径（--source api）：Google Ads API `generateKeywordHistoricalMetrics`。
    2026-08-05 实现（此前「接口留着，实现未接」）。凭据不全时按纪律报缺退出，
    不降级、不静默跳过。词表 = 种子词 ∪ Query 库里还没有任何量证据的词
    （判据与 kp_seeds.py 同：`月搜索量` 与 `搜索量区间` 皆空）。
    ⚠️ 两个结构性事实，API 路径改变不了：
      - 无投放账号返回的仍是桶中值（5×10^n），走与 CSV 同一套文件级桶化判定；
      - >10 词的 keyword 会被整个请求拒绝，先剔除并在报告里点名。
    本端点只做**补量**不做发现——发现面要 generateKeywordIdeas，另立脚本再说。

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

import requests

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

# Google Ads API 版本按年轮换淘汰，写死会在停服日整链断掉。
# 默认值随实现日期取当时最新，.env 里 GOOGLE_ADS_API_VERSION 可覆盖。
# 2026-08-06 实测：v21 已停服（UNSUPPORTED_VERSION，请求直接被拒），
# v22–v25 均在服，取最新的 v25（寿命最长）。
GOOGLE_ADS_API_VERSION = "v25"
KP_MAX_WORDS = 10   # KP 结构上限：>10 词整个请求被拒（2026-08-05 界面实测同款限制）


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


def detect_buckets(values, bucket_cfg):
    """整份文件是不是「桶化」的？返回 (是否桶化, 理由)。

    为什么必须逐文件判、而不是看到 5000 就当区间（2026-08-05 实测发现）：
      KP 的**界面**显示区间（`1K – 10K`），**导出的 CSV** 却把区间换成桶中值
      整数（`5000`）。照单全收就是把桶标签当测量值写进基准——
      Phase 0「折算等于伪造精度」那条禁令在导出层就被绕过去了。
      但有投放的账号返回的是任意整数（4830、12100），那才是真测量值，
      把它降级成区间同样是毁数据。

    判据刻意保守：**每一个**取值都落在桶集合里，且行数达到下限，才算桶化。
    出现任何一个不在表里的数就判为精确值文件——一个反例即证伪。
    """
    buckets = bucket_cfg["buckets"]
    nums = [v for v in values if v is not None]
    if len(nums) < bucket_cfg["detection"]["min_rows"]:
        return False, "有效行 {} 条，少于桶化判定下限 {}，按精确值处理".format(
            len(nums), bucket_cfg["detection"]["min_rows"])
    outliers = sorted({v for v in nums if v not in buckets})
    if outliers:
        return False, ("出现 {} 个不在桶集合里的取值（如 {}），"
                       "判为精确值文件，不做任何还原".format(
                           len(outliers), outliers[:5]))
    return True, ("{} 条取值全部落在桶集合里（出现过 {}），"
                  "判为桶化文件：还原成区间写 `搜索量区间`，`月搜索量` 留空".format(
                      len(nums), sorted(set(nums))))


def kp_key(text):
    """Keyword Planner 侧的宽松去重键：在 sc.norm_query 之上再去尾部标点。

    2026-08-12 修（本次 --commit 亲手踩出来的坑）：sc.norm_query 只做「去首尾空白 +
    压缩内部空白 + 转小写」，**不去标点**。而 Google Ads 会把关键词规范化后再回给你——
    问号被吃掉、大写被压平。于是库里存的
        "How do I find clips from hours of stream footage?"（数据来源=AI 建议）
    与 API 回的
        "how do i find clips from hours of stream footage"
    归一后仍不相等，去重判成「新词」，**新建了一行**。
    2026-08-12 那次写入因此产生 8 条重复行，全部无量（API 对这些长尾问句本来就没数据），
    等于给库里塞了 8 条纯噪音，还把同一个问法拆成两条来源不同的行。

    刻意**不改 sc.norm_query**：那个键被七条链和台账「面向」匹配共用，
    动它等于同时改掉所有链的去重语义与 J1 的台账去重口径，
    blast radius 远超本次要解决的问题。宽松键只在本文件的库内匹配处用，
    且只用作 exact 未命中时的兜底——不会让两个真正不同的词合并。
    """
    return sc.norm_query(text).rstrip("?!.,;:").strip()


def parse_csv_dir(csv_dir, bucket_cfg):
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
            # 先整份读完再判桶化——桶化是**文件级**属性，逐行判不出来。
            staged = []
            for rec in reader:
                kw = (rec.get(kw_col) or "").strip()
                if not kw:
                    continue
                vol, note = parse_volume(rec.get(vol_col))
                staged.append({"query 文本": kw, "月搜索量": vol,
                               "volume_note": note, "source_file": name})

            bucketed, why = detect_buckets([r["月搜索量"] for r in staged],
                                           bucket_cfg)
            if bucketed:
                for r in staged:
                    raw = r["月搜索量"]
                    r["搜索量区间"] = bucket_cfg["buckets"][raw]
                    r["月搜索量"] = None          # 区间不是数字，不塞进 number 字段
                    r["volume_note"] = (
                        "桶化文件：CSV 给的 {} 是 Keyword Planner 的桶中值不是测量值，"
                        "已还原成区间 {!r} 写进 `搜索量区间`，`月搜索量` 留空".format(
                            raw, r["搜索量区间"]))
            else:
                for r in staged:
                    r["搜索量区间"] = None
            rows.extend(staged)

            report.append({"file": name, "encoding": enc,
                           "delimiter": "TAB" if delim == "\t" else ",",
                           "header_line": head_idx + 1,
                           "keyword_column": kw_col, "volume_column": vol_col,
                           "parsed_rows": len(staged), "error": None,
                           "bucketed": bucketed, "bucket_verdict": why})
        except (OSError, ValueError) as exc:
            report.append({"file": name, "parsed_rows": 0, "error": str(exc)})
    return rows, report


# --------------------------------------------------------------------------
# Google Ads API 路径（2026-08-05 实现）
# --------------------------------------------------------------------------

def google_ads_access_token(env):
    """refresh_token → access_token。一次 POST，与 gsc_queries 同一形态，不引 SDK。"""
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError("Google OAuth 换 access_token 失败 HTTP {}：{}".format(
            resp.status_code, resp.text[:400]))
    return resp.json()["access_token"]


def fetch_api_rows(env, keywords, bucket_cfg):
    """generateKeywordHistoricalMetrics → rows。

    返回结构与 parse_csv_dir 逐字段对齐（含桶化判定），下游 create/update 零改动。
    ⚠️ 无投放账号的 API 返回值同样是桶中值（实测 CSV 是 5×10^n，API 是同一套数据），
    所以文件级桶化判定原样适用——API 来的数不因为「来自 API」就当精确值。
    """
    ok, too_long = [], []
    for kw in keywords:
        (ok if len(kw.split()) <= KP_MAX_WORDS else too_long).append(kw)

    staged = []
    note_prefix = ""
    if ok:
        token = google_ads_access_token(env)
        version = env.get("GOOGLE_ADS_API_VERSION") or GOOGLE_ADS_API_VERSION
        cid = env["GOOGLE_ADS_CUSTOMER_ID"].replace("-", "")
        headers = {"Authorization": "Bearer {}".format(token),
                   "developer-token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
                   "Content-Type": "application/json"}
        login_cid = (env.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or "").replace("-", "")
        if login_cid:
            headers["login-customer-id"] = login_cid
        url = ("https://googleads.googleapis.com/{}/customers/{}"
               ":generateKeywordHistoricalMetrics".format(version, cid))
        resp = requests.post(url, json={
            "keywords": ok,
            "keywordPlanNetwork": "GOOGLE_SEARCH",
        }, headers=headers, timeout=60)
        if resp.status_code >= 400:
            text = resp.text[:600]
            hint = ""
            if "DEVELOPER_TOKEN_NOT_APPROVED" in text:
                hint = ("\n→ developer token 还是 Test 级，只能查测试账号。"
                        "去 Google Ads 后台 API Center 申请 Basic 级再跑。")
            elif "USER_PERMISSION_DENIED" in text or "CUSTOMER_NOT_FOUND" in text:
                hint = ("\n→ 检查 GOOGLE_ADS_CUSTOMER_ID 是否是这个 OAuth 账号"
                        "有权限的账号；经 MCC 访问时需另配 GOOGLE_ADS_LOGIN_CUSTOMER_ID。")
            raise RuntimeError("Google Ads API HTTP {}：{}{}".format(
                resp.status_code, text, hint))
        for r in resp.json().get("results") or []:
            kw = (r.get("text") or "").strip()
            if not kw:
                continue
            metrics = r.get("keywordMetrics") or {}
            vol = metrics.get("avgMonthlySearches")
            vol = int(vol) if vol is not None else None
            staged.append({"query 文本": kw, "月搜索量": vol,
                           "volume_note": "" if vol is not None
                           else "API 未返回 avgMonthlySearches（无数据）",
                           "source_file": "google_ads_api"})

    bucketed, why = detect_buckets([r["月搜索量"] for r in staged], bucket_cfg)
    if bucketed:
        for r in staged:
            raw = r["月搜索量"]
            if raw is None:
                r["搜索量区间"] = None
                continue
            r["搜索量区间"] = bucket_cfg["buckets"][raw]
            r["月搜索量"] = None
            r["volume_note"] = (
                "桶化返回：API 给的 {} 是 Keyword Planner 的桶中值不是测量值，"
                "已还原成区间 {!r} 写进 `搜索量区间`，`月搜索量` 留空".format(
                    raw, r["搜索量区间"]))
    else:
        for r in staged:
            r["搜索量区间"] = None

    report = [{"file": "google_ads_api", "encoding": None, "delimiter": None,
               "header_line": None, "keyword_column": "text",
               "volume_column": "keywordMetrics.avgMonthlySearches",
               "parsed_rows": len(staged), "error": None,
               "bucketed": bucketed, "bucket_verdict": why,
               "requested": len(ok),
               "skipped_over_10_words": too_long}]
    return staged, report


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

        bucket_cfg = ac.load_config("kp_buckets.yaml")

        # Query 库现状 → 去重（API 路径还要用它算「无量证据词表」，所以先取）
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        existing_rows = notion.query_all(env["DS_QUERY"])
        existing = {}
        existing_loose = {}
        for r in existing_rows:
            text = ac.title_text(r.get("properties", {}), "query 文本")
            key = sc.norm_query(text)
            if key:
                existing[key] = r
                existing_loose.setdefault(kp_key(text), r)

        # API 路径不碰 data/kw/，csv_dir 保持 None——下方 emit 照实写 null。
        # 2026-08-12 修：原来 csv_dir 只在 else 分支赋值，emit 无条件引用它，
        # API 路径跑到最后一步 UnboundLocalError。这个坑之前被 403 挡在前面看不见
        # （Basic 审批下来、API 真回数据之后才暴露），一崩就是「数据拿回来了但一条没落地」。
        csv_dir = None

        if args.source == "api":
            missing = [k for k in GOOGLE_ADS_KEYS if not env.get(k)]
            if missing:
                sc.missing_credential(
                    SCRIPT, missing,
                    "Google Ads API 路径的凭据不在 .env（缺 {} 项）。"
                    "OAuth 三项用 scripts/ads_auth.py 换取；"
                    "或走降级路径：真人从 Keyword Planner 导出 CSV 放 data/kw/，"
                    "再跑 --source csv。".format(len(missing)), th,
                    extra={"seed_count": len(seeds)})
            # 词表 = 种子词 ∪ 库里还没有任何量证据的词（判据与 kp_seeds.py 一致）
            kw_set, kw_list = set(), []
            for s in seeds:
                key = sc.norm_query(s["query 文本"])
                if key not in kw_set:
                    kw_set.add(key)
                    kw_list.append(s["query 文本"])
            for r in existing_rows:
                props = r.get("properties", {})
                text = ac.title_text(props, "query 文本")
                vol_prop = props.get("月搜索量") or {}
                vol = vol_prop.get("number") if vol_prop.get("type") == "number" else None
                rng = ac.rich_text(props, "搜索量区间").strip()
                key = sc.norm_query(text)
                if text and vol is None and not rng and key not in kw_set:
                    kw_set.add(key)
                    kw_list.append(text)
            parsed, files_report = fetch_api_rows(env, kw_list, bucket_cfg)
        else:
            csv_dir = args.csv_dir or os.path.join(ac.REPO, "data", "kw")
            os.makedirs(csv_dir, exist_ok=True)
            parsed, files_report = parse_csv_dir(csv_dir, bucket_cfg)

        seed_index = {sc.norm_query(s["query 文本"]): s for s in seeds}
        qcfg = scan_cfg["query"]
        ds_value = qcfg["data_source_values"]["keyword_volume"]

        to_create, to_update, skipped = [], [], []
        for row in parsed:
            key = sc.norm_query(row["query 文本"])
            # exact 优先，宽松键兜底（见 kp_key 的注释：KP 会把 ? 与大写吃掉）。
            # 命中宽松键时，**以库里那一行为准**——它是真人/上游链写的原始问法，
            # KP 规范化过的写法不该反过来盖掉它。
            hit = existing.get(key) or existing_loose.get(kp_key(row["query 文本"]))
            matched_loose = key not in existing and hit is not None
            seed = seed_index.get(key)
            typ = seed["类型"] if seed else classify_type(row["query 文本"], scan_cfg)
            item = {
                "query 文本": row["query 文本"],
                "类型": typ,
                "面向角色": qcfg["role_by_type"][typ],
                "月搜索量": row["月搜索量"],
                "搜索量区间": row.get("搜索量区间"),
                "数据来源": ds_value,
                "状态": qcfg["initial_status"],
                "volume_note": row["volume_note"],
                "source_file": row["source_file"],
                "in_seed_list": bool(seed),
                "provenance": seed["provenance"] if seed else
                              "Keyword Planner 导出（非种子词，由 KP 扩展建议带出）【推演待校准】",
            }
            if hit is not None:
                item["existing_page_id"] = hit["id"]
                # 已有行的来源标注要留着，不能被本脚本盖掉。见下方 commit 分支的注释。
                item["existing_数据来源"] = ac.select_name(
                    hit.get("properties", {}), "数据来源")
                if matched_loose:
                    # 宽松键命中要留痕：它是一次「差标点/大小写」的判定，
                    # 不像 exact 那样无可争议，日志里要能看出是哪一行被认成了同一个词。
                    item["matched_loose"] = True
                    item["matched_existing_文本"] = ac.title_text(
                        hit.get("properties", {}), "query 文本")
                # 本次带回了新信息吗？精确值算，区间也算——
                # 区间是「量级已知、精度未知」，比什么都没有强，
                # 它有自己的列（`搜索量区间`，Phase 0 字段表 2026-08-05 解冻加的），
                # 不会挤占 `月搜索量`。
                if row["月搜索量"] is None and not row.get("搜索量区间"):
                    skipped.append(dict(item,
                        skip_reason="已在 Query 库，且本次既无精确搜索量也无区间，不覆盖"))
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
                    "搜索量区间": sc.p_text(item["搜索量区间"]),
                    "数据来源": sc.p_select(item["数据来源"]),
                    "状态": sc.p_select(item["状态"]),
                })
                written.append({"action": "create", "query 文本": item["query 文本"],
                                "page_id": page["id"],
                                "月搜索量": item["月搜索量"],
                                "搜索量区间": item["搜索量区间"]})
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
                # 精确值与区间各写各的列，互不挤占：
                #   有精确值 → 写 `月搜索量`
                #   有区间   → 写 `搜索量区间`（Phase 0 字段表 2026-08-05 解冻加的第 8 列）
                # 只有本次真的带回了那一项才写，别拿 None 去盖掉已有的值。
                props = {}
                if item["月搜索量"] is not None:
                    props["月搜索量"] = sc.p_number(item["月搜索量"])
                if item.get("搜索量区间"):
                    props["搜索量区间"] = sc.p_text(item["搜索量区间"])
                if not item.get("existing_数据来源"):
                    props["数据来源"] = sc.p_select(item["数据来源"])
                notion.update_page(item["existing_page_id"], props)
                written.append({"action": "update", "query 文本": item["query 文本"],
                                "page_id": item["existing_page_id"],
                                "数据来源": item.get("existing_数据来源") or item["数据来源"],
                                "数据来源_是否本次写入": "数据来源" in props,
                                "本次写入的列": sorted(props.keys())})

        sc.emit(SCRIPT, {
            "script": SCRIPT,
            "mode": mode,
            "status": "ok",
            "wrote_notion": mode == "commit",
            "source": args.source,
            # API 路径下如实写 null，不填一个其实没被读过的目录路径——
            # 那会让日志说谎（看上去像是从 data/kw/ 解析出来的）。
            "csv_dir": os.path.relpath(csv_dir, ac.REPO) if csv_dir else None,
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
                "桶化文件的处置：Keyword Planner 的界面显示区间、导出 CSV 却给桶中值整数"
                "（1K – 10K → 5000）。照单全收就是把桶标签当测量值写进基准。"
                "本脚本逐文件判桶化（全部取值都在桶集合里才算），桶化文件一律"
                "`月搜索量` 留空、区间写进 `搜索量区间`（Phase 0 字段表 2026-08-05 解冻加的第 8 列）。",
                "更新已有行时只补本次真的带回来的列；「数据来源」仅在为空时才写，不覆盖已有标注"
                "（2026-08-05 Shawn 拍板改。此前无条件覆写，会把「AI 建议」「A1 扫描」"
                "「买家原话」三种出处静默抹成「Keyword Planner」）。",
            ],
        }, th)
        return sc.EXIT_OK

    except Exception as exc:  # noqa: BLE001 —— 与 Phase 1 同：宁可整体失败，不产出半份结果
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
