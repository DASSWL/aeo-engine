#!/usr/bin/env python3
"""AEO Engine · A1 —— Google Search Console 的真实 query → Query 库。

Shawn 2026-08-05 指定新建。六条链之外的第七条，也是唯一给**精确整数**的一条。

它在几条链里的位置（别搞混，三者回答的是不同问题）：

    Keyword Planner   市场上有多少人搜        —— 桶中值，且拒绝超过 10 词的长尾
    Search Console    真人实际搜了什么、我们露没露面 —— 精确整数，无词数限制
    SERP API          谁在占这些词的位          —— 不给量

⛔⛔ 本脚本最要紧的一条纪律，写在最前面：

    **impressions 不是月搜索量，绝不写进 `月搜索量`。**

    impressions = 「我们出现了多少次」，不是「多少人搜了」。它被两件事过滤过：
    我们有没有内容、Google 排不排我们。一个词一个月一万人搜、我们从没露面 →
    impressions = 0。把它写进 `月搜索量` 比 KP 桶中值更严重——
    桶中值至少还在描述市场，impressions 描述的是我们自己。

    所以本链写进 Query 库的行，`月搜索量` 与 `搜索量区间` **都留空**。
    clicks / impressions / position 只落运行日志与审核清单。

同理它的**发现能力有个硬边界**：站上没有的品类它永远是空的。
2026-08-05 人工看过一轮——739 条 query 里「检索已有素材」这个品类一条都没有，
而那正是 Query 库整库在讲的东西。那不是「没需求」的证据，是「我们没内容」的证据。
两者混同就是拿缺席当反证。要看「市场有需求但我们不沾边」的词，那是 Keyword Planner 的活。

两条路径（同 keyword_volume 的形态：API 路径留着，先用真人导出的 CSV 跑通）：
  * API 路径（--source api）：需要下面三项 OAuth 凭据
  * 降级路径（--source csv，**默认**）：真人从 GSC 界面 EXPORT 下来的
    Queries.csv 或整包 .zip 放 data/gsc/，本脚本解析。零凭据。

凭据（API 路径用；缺任一即以退出码 2 退出，不降级不造假）：
    GSC_CLIENT_ID / GSC_CLIENT_SECRET / GSC_REFRESH_TOKEN   OAuth2
    GSC_SITE_URL                                            可选，缺则用 config 的 api.site_url

用法：
    python3 scripts/gsc_queries.py            # dry-run（默认 csv 路径），打印候选与品牌分布
    python3 scripts/gsc_queries.py --source api   # 走 API（需凭据）
    python3 scripts/gsc_queries.py --review    # 同时把审核清单写进 outbox
    python3 scripts/gsc_queries.py --commit    # 真写 Query 库（需 config approved）
"""

import csv
import glob
import io
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402
# 类型判定复用 keyword_volume 的实现，不另写一份（只读不改，未动该文件一行）。
from keyword_volume import classify_type   # noqa: E402

SCRIPT = "gsc_queries"

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://searchconsole.googleapis.com/webmasters/v3/sites/{}/searchAnalytics/query"
CRED_KEYS = ("GSC_CLIENT_ID", "GSC_CLIENT_SECRET", "GSC_REFRESH_TOKEN")

NON_WORD = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# API
#
# 刻意只用 requests，不引 google-auth / google-api-python-client。
# 理由同 aeo_common 开头那句「不用第三方库，避免多一个依赖」——
# refresh_token 换 access_token 就是一次 POST，为它装一整套 SDK 不值。
# ---------------------------------------------------------------------------

def access_token(session, env):
    resp = session.post(TOKEN_URL, data={
        "client_id": env["GSC_CLIENT_ID"],
        "client_secret": env["GSC_CLIENT_SECRET"],
        "refresh_token": env["GSC_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError("OAuth 换 token 失败 HTTP {}：{}".format(
            resp.status_code, resp.text[:300]))
    tok = resp.json().get("access_token")
    if not tok:
        raise RuntimeError("OAuth 响应里没有 access_token：{}".format(
            str(resp.json())[:200]))
    return tok


def fetch_rows(session, token, site_url, api_cfg, tz):
    """拉 searchAnalytics。分页取到取尽或够 row_limit 为止。"""
    end = datetime.now(tz).date()
    start = end - timedelta(days=api_cfg["lookback_days"])
    url = API_URL.format(requests.utils.quote(site_url, safe=""))
    out, start_row = [], 0
    while len(out) < api_cfg["row_limit"]:
        page = min(25000, api_cfg["row_limit"] - len(out))
        resp = session.post(url, json={
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": api_cfg["dimensions"],
            "type": api_cfg["search_type"],
            "rowLimit": page,
            "startRow": start_row,
        }, headers={"Authorization": "Bearer {}".format(token)}, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError("Search Console API HTTP {}：{}".format(
                resp.status_code, resp.text[:400]))
        rows = resp.json().get("rows") or []
        out.extend(rows)
        if len(rows) < page:
            break
        start_row += len(rows)
    return out, start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------------
# 降级路径：解析 GSC 界面导出的 CSV
#
# 为什么需要它：拿 OAuth 凭据要过 Google Cloud 建项目 / 配同意屏那几步，
# 是真人动作。而品牌判定的阈值必须先跑在**全量真实 query** 上才知道准不准——
# 那恰恰是 approved 之前该看的东西。等凭据就等于把校准无限后推。
# 同 Phase 2 对 keyword_volume 的处置：API 路径留着，先用 CSV 跑通。
#
# ⚠️ 导出的 CSV **带着界面当时的过滤器**。要校准品牌判定就必须导**不带过滤**的全量，
#    否则等于拿已经被 Google 滤过一遍的样本去验我们自己的滤器。
# ---------------------------------------------------------------------------

GSC_QUERY_COLUMNS = ("top queries", "query", "queries", "热门查询", "查询")
GSC_NUM_COLUMNS = {
    "clicks": ("clicks", "点击次数", "点击"),
    "impressions": ("impressions", "展示次数", "曝光"),
    "position": ("position", "average position", "平均排名", "排名"),
}


def _pick_col(fieldnames, candidates):
    for name in fieldnames or []:
        if (name or "").strip().lower().lstrip("\ufeff") in candidates:
            return name
    return None


def _num(raw):
    """'1,234' → 1234；'6.2%' → 6.2；空 → 0。认不出就抛，不猜。"""
    s = (raw or "").strip().replace(",", "").replace("%", "")
    if not s:
        return 0
    try:
        f = float(s)
    except ValueError:
        raise ValueError("认不出的数值：{!r}".format(raw))
    return int(f) if f.is_integer() else f


def _csv_texts(path):
    """返回 [(来源名, 文本)]。.zip 就从包里挑 query 那份，.csv 直接读。"""
    if path.lower().endswith(".zip"):
        out = []
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if not n.lower().endswith(".csv"):
                    continue
                # GSC 的包里有 Queries / Pages / Countries / Devices / Dates / Filters
                # 六份，只要 query 那份。认名字，不靠顺序。
                base = os.path.basename(n).lower()
                if "quer" not in base and "查询" not in base:
                    continue
                out.append(("{}!{}".format(os.path.basename(path), n),
                            z.read(n).decode("utf-8-sig")))
        if not out:
            raise ValueError("{} 里没有名字含 quer 的 CSV——"
                             "GSC 导出包应含 Queries.csv".format(os.path.basename(path)))
        return out
    with open(path, "rb") as fh:
        return [(os.path.basename(path), fh.read().decode("utf-8-sig"))]


def parse_csv_dir(csv_dir):
    """解析 data/gsc/ 下的 CSV/ZIP → 与 API 同形状的 rows。"""
    paths = sorted(glob.glob(os.path.join(csv_dir, "*.csv")) +
                   glob.glob(os.path.join(csv_dir, "*.zip")))
    rows, report = [], []
    for path in paths:
        try:
            for name, text in _csv_texts(path):
                reader = csv.DictReader(io.StringIO(text))
                qcol = _pick_col(reader.fieldnames, GSC_QUERY_COLUMNS)
                if not qcol:
                    raise ValueError("找不到 query 列，表头是 {}".format(reader.fieldnames))
                cols = {k: _pick_col(reader.fieldnames, v)
                        for k, v in GSC_NUM_COLUMNS.items()}
                n = 0
                for rec in reader:
                    q = (rec.get(qcol) or "").strip()
                    if not q:
                        continue
                    clicks = _num(rec.get(cols["clicks"])) if cols["clicks"] else 0
                    impr = _num(rec.get(cols["impressions"])) if cols["impressions"] else 0
                    pos = _num(rec.get(cols["position"])) if cols["position"] else 0
                    # 转成与 API 完全相同的形状，下游一行都不用改
                    rows.append({"keys": [q], "clicks": clicks, "impressions": impr,
                                 "ctr": (clicks / impr) if impr else 0,
                                 "position": pos})
                    n += 1
                report.append({"file": name, "query_column": qcol,
                               "num_columns": cols, "parsed_rows": n, "error": None})
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            report.append({"file": os.path.basename(path), "parsed_rows": 0,
                           "error": str(exc)})
    return rows, report


# ---------------------------------------------------------------------------
# 品牌判定
# ---------------------------------------------------------------------------

def despace(text):
    return NON_WORD.sub("", (text or "").lower())


def brand_score(query, brand_cfg, vocab=None):
    """返回 (最高相似度, 命中的核心词)。只跟**核心 token** 比，且跳过通用词。

    2026-08-05 首版实测踩到的坑：把复合品牌名 "vivuvideo" 拿去跟通用 token
    "video" 算相似度得 0.714，于是**凡是含 video 的真词全被判成品牌**——
    16 条测试真词误杀 13 条，而且全部同分，一眼就看出是规则错不是阈值错。
    修法是两条：复合形态改走子串匹配（见 is_brand），模糊匹配只用核心 token
    且把通用词排除在候选之外。
    """
    generic = {g.lower() for g in brand_cfg.get("generic_tokens") or []}
    flat = despace(query)
    tokens = [t for t in NON_WORD.split((query or "").lower()) if t and t not in generic]
    # 英文词永远不是品牌拼写变体——变体按定义是造出来的词。
    # 不加这条会误杀 live video / view count / visual ai / vivid ai（实测）。
    if vocab and brand_cfg.get("skip_dictionary_words"):
        real_words = [t for t in tokens if t in vocab]
        tokens = [t for t in tokens if t not in vocab]
        # 非通用 token **全是**英文词 → 整串也不比。
        # 整串比对本来只为逮住被空格拆开的品牌（"vi you ai" → "viyouai"，
        # 其中 "vi" 不是词），拿它去比一句全英文的短语只会误伤：
        # 实测 "visual ai"(flat 0.600) / "vivid ai"(flat 0.667) 都被这一步误杀过。
        if real_words and not tokens:
            return 0.0, None
    best, hit = 0.0, None
    for term in brand_cfg["core_terms"]:
        for cand in [flat] + tokens:
            if not cand:
                continue
            r = SequenceMatcher(None, term, cand).ratio()
            if r > best:
                best, hit = r, term
    return best, hit


def is_brand(query, brand_cfg, vocab=None):
    """判品牌。四段：强制表 → 独立 token → 子串命中 → 长度分档的模糊匹配。"""
    low = " ".join((query or "").split()).lower()
    if low in {s.lower() for s in brand_cfg.get("force_not_brand") or []}:
        return False, 0.0, "force_not_brand"
    if low in {s.lower() for s in brand_cfg.get("force_brand") or []}:
        return True, 1.0, "force_brand"

    toks = {t for t in NON_WORD.split(low) if t}
    for t in brand_cfg.get("standalone_tokens") or []:
        if t.lower() in toks:
            return True, 1.0, "standalone:{}".format(t)
    # 第三方品牌里恰好是英文词的那一类。表由真人填，脚本不推断——见 config ⑦。
    for t in brand_cfg.get("third_party_tokens") or []:
        if t.lower() in toks:
            return True, 1.0, "third_party:{}".format(t)

    flat = despace(query)
    for term in brand_cfg.get("contains_terms") or []:
        if despace(term) and despace(term) in flat:
            return True, 1.0, "contains:{}".format(term)

    score, hit = brand_score(query, brand_cfg, vocab)
    # 短串与长短语用不同的门槛：去空格后很短的串跟品牌名有一半像，
    # 几乎必然是拼写变体；长短语则要求高得多，否则误杀真词。
    thr = (brand_cfg["short_threshold"]
           if len(flat) <= brand_cfg["short_max_chars"]
           else brand_cfg["long_threshold"])
    return score >= thr, score, hit


def hits_marker(text, markers):
    low = " ".join((text or "").split()).lower()
    for m in markers:
        if re.search(r"(?<!\w){}(?!\w)".format(re.escape(m.lower())), low):
            return m
    return None


# ---------------------------------------------------------------------------
# 造词过滤：把第三方品牌挡在外面，且不点任何竞品名
#
# 2026-08-05 首轮真实 dry-run：30 条候选里 25 条是别人家的品牌
# （viyou / vuvido / visla / vrew / vidifyai …）。我们靠相似域名排在那些词上，
# 而品牌过滤器只认我们自己的品牌。
#
# 不列竞品名单的理由是硬约束：gates.yaml 的 competitor_list_converged = false，
# 且明文禁止凭猜测指定。脚本里写死竞品名 = 用搜索数据替 J0 把那个判断做了。
#
# 改用结构判据：第三方品牌都含**造出来的词**，真 query 全由英文词组成。
# ---------------------------------------------------------------------------

def load_vocab(neo_cfg):
    """载入词典。返回 (词集合 或 None, 状态说明)。

    ⚠️ 文件不在时**不静默放行**。整条规则停用，并把原因一路带到报告最上方——
    「没报错」被读成「过滤过了」是 Phase 3 §七⑦ 那一类隐患，这里不许重演。
    """
    if not neo_cfg.get("enabled"):
        return None, "已在 config 里关闭（neologism.enabled=false）——造词过滤未生效"
    path = neo_cfg["wordlist_path"]
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            words = {w.strip().lower() for w in fh if w.strip()}
    except OSError as exc:
        return None, ("⚠️ 词典 {} 读不到（{}）——**造词过滤整条停用**，"
                      "第三方品牌名会原样进候选。别把这次的输出当过滤过的。"
                      .format(path, exc))
    if len(words) < 1000:
        return None, ("⚠️ 词典 {} 只有 {} 个词，不像正常词表——整条规则停用，"
                      "宁可不过滤也不要用一份坏词典误杀真 query。".format(path, len(words)))
    return words, "词典 {} 载入 {} 词".format(path, len(words))


def unknown_tokens(query, vocab, neo_cfg):
    """返回该 query 里**不认识**的 token。空列表 = 全都认识。"""
    allow = {a.lower() for a in neo_cfg.get("allow_tokens") or []}
    irreg = {k.lower() for k in (neo_cfg.get("irregular") or {})}
    sufs = neo_cfg.get("stem_suffixes") or []
    out = []
    for t in NON_WORD.split((query or "").lower()):
        if not t or t.isdigit():
            continue
        if t in allow or t in irreg or t in vocab:
            continue
        # 词形还原：web2 只收原形，不还原会误杀 cuts / hours / spoken 这类。
        stemmed = False
        for suf in sufs:
            if t.endswith(suf) and len(t) > len(suf) and t[:-len(suf)] in vocab:
                stemmed = True
                break
        if not stemmed and t.endswith("ing") and (t[:-3] + "e") in vocab:
            stemmed = True
        if not stemmed and t.endswith("ed") and t[:-1] in vocab:
            stemmed = True
        if not stemmed:
            out.append(t)
    return out


def has_non_ascii(query):
    return any(ord(ch) > 127 for ch in (query or ""))


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------

def screen(rows, cfg, scan_cfg, vocab):
    """GSC 行 → (候选, 品牌行, 被过滤行)。每条被丢的都说明理由。"""
    f, b = cfg["filters"], cfg["brand"]
    neo = cfg.get("neologism") or {}
    qcfg = scan_cfg["query"]
    kept, branded, third_party, dropped = [], [], [], []

    for r in rows:
        q = (r.get("keys") or [""])[0].strip()
        if not q:
            continue
        item = {
            "query 文本": q,
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        brand, score, hit = is_brand(q, b, vocab)
        item["brand_score"] = round(score, 3)
        item["brand_hit"] = hit
        if brand:
            # 第三方品牌单独一桶：混进「我方品牌」会把 branded search 基线虚高，
            # 那个比例是 A7 的度量项，掺了别人家的流量就不再是我们的基线。
            if str(hit).startswith("third_party:"):
                third_party.append(item)
            else:
                branded.append(item)
            continue

        if item["impressions"] < f["min_impressions"]:
            dropped.append(dict(item, drop_reason="曝光 {} < min_impressions {}".format(
                item["impressions"], f["min_impressions"])))
            continue
        words = q.split()
        if not (f["min_words"] <= len(words) <= f["max_words"]):
            dropped.append(dict(item, drop_reason="词数 {} 不在 [{}, {}]".format(
                len(words), f["min_words"], f["max_words"])))
            continue
        bad = hits_marker(q, f["exclude_markers"])
        if bad:
            dropped.append(dict(item, drop_reason="命中排除词 {!r}".format(bad)))
            continue

        # 造词过滤。vocab 为 None 表示规则停用（原因已在报告最上方大声说过）。
        if vocab is not None:
            if has_non_ascii(q):
                dropped.append(dict(item, drop_reason=neo["non_ascii_reason"]))
                continue
            unk = unknown_tokens(q, vocab, neo)
            if unk:
                dropped.append(dict(item,
                    drop_reason="含造出来的词 {}——多半是第三方品牌名。"
                                "若确属正常词汇，加进 config neologism.allow_tokens"
                                .format(unk),
                    unknown_tokens=unk))
                continue

        typ = classify_type(q, scan_cfg)
        item["类型"] = typ
        item["面向角色"] = qcfg["role_by_type"][typ]
        item["状态"] = qcfg["initial_status"]
        item["数据来源"] = cfg["data_source_value"]
        kept.append(item)

    kept.sort(key=lambda x: -x["impressions"])
    third_party.sort(key=lambda x: -x["impressions"])
    return kept, branded, third_party, dropped


def baseline(branded, non_branded_all):
    """branded search 基线。Concept 里 A7 的度量项，当前没有任何脚本在算。

    顺手算出来的——数据是同一次 API 调用带回来的，不额外花任何成本。
    """
    def agg(rows):
        return {"queries": len(rows),
                "clicks": sum(r["clicks"] for r in rows),
                "impressions": sum(r["impressions"] for r in rows)}
    b, n = agg(branded), agg(non_branded_all)
    total_c = b["clicks"] + n["clicks"]
    total_i = b["impressions"] + n["impressions"]
    return {
        "branded": b, "non_branded": n,
        "branded_click_share": round(b["clicks"] / total_c * 100, 1) if total_c else None,
        "branded_impression_share": round(b["impressions"] / total_i * 100, 1) if total_i else None,
        "note": "品牌判定是模糊匹配（见 config/gsc.yaml brand），阈值变了这两个比例就会变。"
                "拿它做趋势时必须锁住阈值，否则是在量自己的参数不是量市场。",
    }


def render(r):
    c = r["counts"]
    bl = r["branded_baseline"]
    L = ["🔍 AEO · Search Console 真实 query {}".format(r["generated_at"][:16]),
         "",
         "窗口：{} → {}　property：`{}`".format(
             r["window"]["start"], r["window"]["end"], r["site_url"]),
         "",
         "| 项 | 值 |", "|---|---|",
         "| API 返回 query 数 | {} |".format(c["api_rows"]),
         "| 判为我方品牌（含拼写变体） | {} |".format(c["branded"]),
         "| 判为第三方品牌（真人点名） | {} |".format(c.get("third_party_brand", 0)),
         "| 过滤掉 | {} |".format(c["dropped"]),
         "| 与 Query 库重复 | {} |".format(c["duplicate"]),
         "| **待写入** | **{}** |".format(c["to_create"]),
         "| 被 max_per_run 截下 | {} |".format(c["capped"]),
         "| 规则文件状态 | `{}` |".format(r["config_status"]),
         "| 造词过滤 | {} |".format(
             "✅ " + r["neologism_filter"]["status"]
             if r["neologism_filter"]["active"]
             else "❌ **未生效** —— " + r["neologism_filter"]["status"]),
         "",
         "**branded search 基线**（Concept A7 度量项，此前无人计算）：",
         "",
         "| | query 数 | 点击 | 曝光 |",
         "|---|---:|---:|---:|",
         "| 品牌 | {queries} | {clicks} | {impressions} |".format(**bl["branded"]),
         "| 非品牌 | {queries} | {clicks} | {impressions} |".format(**bl["non_branded"]),
         "",
         "品牌占点击 **{}%**、占曝光 **{}%**。".format(
             bl["branded_click_share"], bl["branded_impression_share"]),
         ""]

    if r["to_create"]:
        L += ["## 待写入（`数据来源 = Search Console` · 月搜索量与区间**均留空**）", ""]
        for it in r["to_create"]:
            L.append("- **{}** — {} 次曝光 / {} 点击 / 排名 {} · {} / {}".format(
                it["query 文本"], it["impressions"], it["clicks"],
                it["position"], it["类型"], it["面向角色"]))
        L.append("")

    if r["dropped_as_brand_borderline"]:
        L += ["## 判成品牌但分数接近阈值的（可能误杀，真人捞）", ""]
        for it in r["dropped_as_brand_borderline"]:
            L.append("- {!r} — 相似度 {} vs `{}`（{} 曝光）".format(
                it["query 文本"], it["brand_score"], it["brand_hit"],
                it["impressions"]))
        L.append("")

    if r.get("possible_third_party"):
        L += ["## 疑似第三方品牌（词典词，两道滤器都拦不住，需真人点名）", "",
              "填进 `config/gsc.yaml` 的 `brand.third_party_tokens` 即可拦掉。",
              "**脚本刻意不自己填**：从搜索数据认竞品正是 "
              "`competitor_list_converged = false` 那条禁令要防的推断。", ""]
        for t, qs in r["possible_third_party"]:
            L.append("- `{}` — 出现在 {} 条候选里：{}".format(
                t, len(qs), "、".join(qs[:4])))
        L.append("")

    L += ["> ⛔ **impressions 不是月搜索量。** 它是「我们出现了多少次」，",
          "> 被我们有没有内容、Google 排不排我们过滤过。所以这些行进库时",
          "> `月搜索量` 与 `搜索量区间` 都留空——数字只在本清单与运行日志里。",
          "",
          "> ⛔ **本链看不见我们没有内容的品类。** 它的沉默不是「没需求」的证据。",
          "> 要找「市场有需求但我们完全不沾边」的词，那是 Keyword Planner 的活。", ""]
    return "\n".join(L)


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--review", action="store_true",
                        help="把审核清单写进 outbox（由 outbox_sweep 转发）。不写 Notion")
    parser.add_argument("--source", choices=("csv", "api"), default="csv",
                        help="csv=解析 data/gsc/ 的 GSC 导出（默认，零凭据）；"
                             "api=Search Console API（需 OAuth 三项）")
    parser.add_argument("--csv-dir", default=None, help="默认 data/gsc/")
    args = parser.parse_args()
    mode = sc.resolve_mode(args)

    th = ac.load_config("thresholds.yaml")
    try:
        cfg = ac.load_config("gsc.yaml")
        scan_cfg = ac.load_config("scan.yaml")
        status = cfg["meta"]["status"]

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])

        env = ac.load_env()
        site_url = env.get("GSC_SITE_URL") or cfg["api"]["site_url"]

        missing = [k for k in CRED_KEYS if not env.get(k)]
        if args.source == "api" and missing:
            sc.missing_credential(
                SCRIPT, missing,
                "Search Console API 的 OAuth 凭据不在 .env（缺 {} 项：{}）。"
                "GSC API 本身免费，但要先在 Google Cloud 建项目、启用 "
                "Search Console API、配 OAuth 同意屏、跑一次授权换出 refresh_token——"
                "那几步是真人动作，脚本代不了。"
                "本次不降级、不造假数据，按纪律以退出码 2 退出。".format(
                    len(missing), " / ".join(missing)), th,
                extra={"mode": mode, "site_url": site_url,
                       "config_status": status,
                       "plan": {"window_days": cfg["api"]["lookback_days"],
                                "row_limit": cfg["api"]["row_limit"],
                                "dimensions": cfg["api"]["dimensions"]}})

        if args.source == "api":
            session = requests.Session()
            token = access_token(session, env)
            rows, start, end = fetch_rows(session, token, site_url, cfg["api"], tz)
            files_report = None
        else:
            csv_dir = args.csv_dir or os.path.join(ac.DATA_DIR, "gsc")
            os.makedirs(csv_dir, exist_ok=True)
            rows, files_report = parse_csv_dir(csv_dir)
            # 窗口由导出时界面上选的日期决定，文件里带不出来——不编。
            start = end = "见导出文件（CSV 不含日期范围）"

        vocab, vocab_note = load_vocab(cfg.get("neologism") or {})
        kept, branded, third_party, dropped = screen(rows, cfg, scan_cfg, vocab)

        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        existing = sc.existing_query_texts(notion.query_all(env["DS_QUERY"]))
        fresh, duplicate = [], []
        for it in kept:
            key = sc.norm_query(it["query 文本"])
            if key in existing:
                duplicate.append(dict(it, dup_reason="Query 库已有同名 query"))
            else:
                fresh.append(it)
                existing.add(key)

        cap = cfg["caps"]["max_per_run"]
        to_create, capped = fresh[:cap], fresh[cap:]

        if mode == "commit" and status != "approved":
            raise RuntimeError(
                "config/gsc.yaml 的 meta.status 是 {!r}，不是 approved。"
                "品牌模糊匹配阈值与过滤参数全是【推演待校准】——"
                "阈值定错会让品牌垃圾灌进 Query 库，或者把真词全误杀。"
                "先跑 --review 出清单，真人对着 dropped_as_brand 核一遍再改 approved。"
                .format(status))

        written = []
        if mode == "commit":
            for it in to_create:
                page = notion.create_page(env["DS_QUERY"], {
                    "query 文本": sc.p_title(it["query 文本"]),
                    "类型": sc.p_select(it["类型"]),
                    "面向角色": sc.p_select(it["面向角色"]),
                    # 两列都留空：impressions 不是搜索量，也不是区间。
                    "月搜索量": sc.p_number(None),
                    "搜索量区间": sc.p_text(None),
                    "数据来源": sc.p_select(it["数据来源"]),
                    "状态": sc.p_select(it["状态"]),
                })
                written.append({"action": "create", "query 文本": it["query 文本"],
                                "page_id": page["id"],
                                "impressions": it["impressions"],
                                "clicks": it["clicks"], "position": it["position"]})

        # 「接近阈值」要按每条各自的档位算——阈值 2026-08-05 改成按长度分档之后，
        # 拿单一阈值去比会把短串和长短语混在一起，算出来的 borderline 没有意义。
        bcfg = cfg["brand"]
        def _thr(q):
            return (bcfg["short_threshold"]
                    if len(despace(q)) <= bcfg["short_max_chars"]
                    else bcfg["long_threshold"])
        borderline = sorted(
            [b for b in branded
             if b["brand_hit"] and not str(b["brand_hit"]).startswith(
                 ("contains:", "force", "standalone:"))
             and b["brand_score"] < _thr(b["query 文本"]) + 0.08],
            key=lambda x: -x["impressions"])[:15]

        # 候选里反复出现的非通用 token —— 疑似第三方品牌，交真人点名。
        # 判据纯粹是「重复出现」，不含任何关于「谁是竞品」的判断。
        tok_map = {}
        generic = {g.lower() for g in cfg["brand"].get("generic_tokens") or []}
        # allow_tokens 是真人确认过的正常词汇，不该出现在「疑似品牌」清单里
        generic |= {a.lower() for a in (cfg.get("neologism") or {}).get("allow_tokens") or []}
        for it in fresh:
            for t in set(NON_WORD.split(it["query 文本"].lower())):
                if t and t not in generic and len(t) > 2 and not t.isdigit():
                    tok_map.setdefault(t, []).append(it["query 文本"])
        possible_third_party = sorted(
            [(t, qs) for t, qs in tok_map.items() if len(qs) >= 2],
            key=lambda x: -len(x[1]))

        result = {
            "script": SCRIPT, "mode": mode, "status": "ok",
            "wrote_notion": mode == "commit",
            "config_status": status,
            "generated_at": datetime.now(tz).isoformat(),
            "source": args.source,
            "neologism_filter": {"active": vocab is not None, "status": vocab_note},
            "files": files_report,
            "site_url": site_url,
            "window": ({"start": start, "end": end,
                        "days": cfg["api"]["lookback_days"]}
                       if args.source == "api"
                       else {"start": start, "end": end,
                             "note": "CSV 路径的窗口取决于导出时界面选的日期范围，"
                                     "文件里带不出来。要确认请看导出时的界面。"}),
            "counts": {
                "api_rows": len(rows),
                "branded": len(branded),
                "third_party_brand": len(third_party),
                "dropped": len(dropped),
                "duplicate": len(duplicate),
                "to_create": len(to_create),
                "capped": len(capped),
            },
            "branded_baseline": baseline(branded, kept + dropped),
            "third_party_brand_rows": third_party,
            "possible_third_party": possible_third_party,
            "to_create": to_create,
            "capped": capped,
            "duplicate": duplicate,
            "dropped": dropped,
            "dropped_as_brand_borderline": borderline,
            "written": written,
            "discipline": {
                "impressions_不是月搜索量":
                    "写进 Query 库的行 `月搜索量` 与 `搜索量区间` 均留空。"
                    "impressions 是「我们出现了多少次」，被我们有没有内容、"
                    "Google 排不排我们过滤过，不是市场需求量。",
                "本链的发现边界":
                    "只看得见我们已经露过面的词。站上没有的品类它永远是空的，"
                    "它的沉默不是「没需求」的证据。",
            },
            "schema_gap": {
                "问题": "Query 库 8 个字段装不下 clicks / impressions / position",
                "本次处置": "只落 logs/{}_*.json 与审核清单".format(SCRIPT),
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
