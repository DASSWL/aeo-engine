#!/usr/bin/env python3
"""AEO Engine · A1 —— Query 库进料链诊断。

回答一个问题：**query 从哪来，现在还来不来。**

为什么需要它：Query 库现在 10 行，全部 `数据来源 = 探测问题`，月搜索量全空。
这 10 行是 `probe_questions_sync.py` 跑了一次写进去的，之后再没有新词进来。
而 J1 第 5 类「痛点级 query 的 AEO 内容」的输入就是这个库——库不长，
那条产线就永远只能对着这 10 条我们自己写的问题写内容。

Query 库的 `数据来源` 每个合法取值对应一条进料链（Phase 0 冻结四个，
2026-08-05 经 Shawn 拍板 additive 解冻加了「A1 扫描」「AI 建议」两个）。本脚本逐条判定：
  • 现在能不能产**新** query（建行），还是只能改已有行
  • 断在哪一环：缺凭据 / 缺调度 / 缺脚本 / 缺 schema / 缺真人动作
  • 修通需要什么，其中哪些是真人的决定

把这五种「断」分开是本脚本存在的全部理由。混在一起谈只会得出
「都做一遍」这种没用的结论——它们的修法完全不同，代价也完全不同。

另一个刻意的区分：**补量 ≠ 发现。**
  补量 = 给我们已经想到的词查搜索量。种子来自 config，config 不变就不产新词。
  发现 = 能带回我们没想到的词。
一条链能不能建行，和它能不能发现，是两件事。

只读、零成本、零对外发送。不写 Notion，不调任何计费 API。

用法：
    python3 scripts/query_intake_health.py              # 打印报告
    python3 scripts/query_intake_health.py --commit     # 同时写 outbox（由 outbox_sweep 转发）
"""

import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "query_intake_health"

# ---------------------------------------------------------------------------
# 各条链的结构事实。
#
# 这些不是阈值、不是可调参数，是「哪个脚本写哪个 数据来源 值」这一层的代码事实，
# 所以写在脚本里而不是 config——写进 config 会暗示它们可以改，而改它们的唯一
# 正确方式是改那个脚本。
#
# `creates_rows` 是逐个读脚本源码确认的，不是照文档抄的：
#   probe_questions_sync.py:96   notion.create_page(...)          → 建行
#   keyword_volume.py:287        notion.create_page(...)          → 建行
#   keyword_volume.py:298        notion.update_page(...)          → 也能改行
#   serp_scan.py:189             notion.update_page(page_id, {数据来源})  → 只改，不建
# ---------------------------------------------------------------------------
CHAINS = [
    {
        "数据来源": "探测问题",
        "writer": "scripts/probe_questions_sync.py",
        "creates_rows": True,
        "updates_rows": False,
        "input_kind": "config",
        "input_desc": "config/scan.yaml 的 probe.questions（真人审过的定稿问题）",
        "credentials": [],
        "kind": "既不补量也不发现",
        "kind_why": "它是**同步**不是生成：把 scan.yaml 里已定稿的问题搬进库。"
                    "config 不变就不产新词，而 config 是我们自己写的。"
                    "月搜索量固定写 null，所以连补量都不算。",
    },
    {
        "数据来源": "Keyword Planner",
        "writer": "scripts/keyword_volume.py",
        "creates_rows": True,
        "updates_rows": True,
        "input_kind": "csv",
        "input_desc": "data/kw/ 下真人从 Keyword Planner 手动导出的 CSV",
        "credentials": ["GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID",
                        "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN",
                        "GOOGLE_ADS_CUSTOMER_ID"],
        "kind": "补量为主，有限发现",
        "kind_why": "28 条种子全部来自我们自己的 config（spec 首批 3 条 + "
                    "segments.yaml 五段 linkedin_keywords 25 条），对种子而言纯补量。"
                    "但脚本对 CSV 里**不在种子表**的行也建行（in_seed_list=false），"
                    "所以真人若导出 KP 的扩展建议，确实能带回我们没想到的词——"
                    "限度是：那是工具的联想 + 真实搜索量，不是任何买家说过的话。",
    },
    {
        "数据来源": "SERP 观察",
        "writer": "scripts/serp_scan.py",
        "creates_rows": False,
        "updates_rows": True,
        "input_kind": "query_db",
        "input_desc": "Query 库按月搜索量前 N（即它的输入就是这个库自己）",
        "credentials": ["SERPAPI_KEY"],
        "kind": "都不是",
        "kind_why": "它不建行——结构上不可能产新 query。它的输入是 Query 库自己，"
                    "产出是「谁在占这些词的位」（竞品与评测站），"
                    "那是给 J0 竞替名单和 J1 对比页用的，不是新词。",
    },
    {
        # Phase 0 字段表 2026-08-05 解冻，additive 加的两个取值之一。
        "数据来源": "AI 建议",
        "writer": "scripts/query_candidates.py",
        "creates_rows": True,
        "updates_rows": False,
        "input_kind": "pool",
        "input_desc": "data/query_candidates.jsonl（每日探测追问收来的候选池）",
        "credentials": [],
        "kind": "假设源，不是发现",
        "kind_why": "它能带回我们没想到的**问法**，这一点是真的。但它是模型说"
                    "「人们常这么问」，不是任何人真的搜过——没有市场证据。"
                    "Shawn 2026-08-05 拍板它直接进库标「候选」、月搜索量留空，"
                    "所以 Query 库里会同时躺着有证据的词和没证据的词，靠本列区分。"
                    "它要变成发现，必须走完 Keyword Planner 那一步拿到真实搜索量。",
    },
    {
        # Phase 0 字段表 2026-08-05 第三次解冻加的取值。
        "数据来源": "Search Console",
        "writer": "scripts/gsc_queries.py",
        "creates_rows": True,
        "updates_rows": False,
        "input_kind": "gsc",
        "input_desc": "Search Console API（真人实际搜了什么、且 vivu.ai 露过面）",
        "credentials": ["GSC_CLIENT_ID", "GSC_CLIENT_SECRET", "GSC_REFRESH_TOKEN"],
        "kind": "真·发现，但有边界",
        "kind_why": "真人实际敲进 Google 的词，精确整数、无词数限制、免费——"
                    "三条里唯一不经任何估算的。但它**只看得见我们已经露过面的词**："
                    "站上没有的品类它永远是空的，它的沉默不是「没需求」的证据。"
                    "找「市场有需求但我们完全不沾边」的词是 Keyword Planner 的活。"
                    "另：impressions 不是搜索量，本链进库的行两个量列都留空。",
    },
    {
        # 解冻加的第二个取值。Reddit / LinkedIn 扫描已经在逐字采集真实提问，
        # 但把它们变成 query 的脚本还没建——这是当前唯一「有值无链」的一条。
        "数据来源": "A1 扫描",
        "writer": "scripts/scan_queries.py",
        "creates_rows": True,
        "updates_rows": False,
        "input_kind": "pipeline",
        "input_desc": "水箱里 `来源 = A1 扫描` 的行的「信号原文」（Reddit 存量提问帖等）",
        "credentials": [],
        "kind": "真·发现",
        "kind_why": "Reddit 存量提问帖的标题就是活人打出来的问句，"
                    "而 scan_reddit_weekly.md 已经写死「原文照抄、不改写不翻译不概括」。"
                    "出处是帖子永久链接——一个公网 URL，谁都能点开核对。"
                    "六条链里出处最硬的一条。",
    },
    {
        "数据来源": "买家原话",
        "writer": "scripts/buyer_quote_queries.py",
        "creates_rows": True,
        "updates_rows": False,
        "input_kind": "winloss",
        "input_desc": "win/loss 库的「买家原话」列",
        "credentials": [],
        "kind": "最强的真·发现",
        "kind_why": "以**真实对话**为源：买家实际怎么说，不是我们觉得他会怎么搜，"
                    "也不是模型说人们会怎么问。四条来源里唯一不经任何中介的一条。",
    },
]


# ---------------------------------------------------------------------------
# 事实采集
# ---------------------------------------------------------------------------

def cron_names():
    """本机 OpenClaw 定时任务名单。取不到就如实说取不到，不猜。"""
    # 超时给足：openclaw 要起 gateway，冷启动时 60s 不够，实测超时过一次。
    # 超时不是「没有定时任务」，两者混同会让报告说假话，所以失败必须带上原因。
    try:
        out = subprocess.run(["openclaw", "cron", "list"],
                             capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None, "openclaw cron list 超时（180s）——查不到不等于没有，别按「无定时任务」读"
    except OSError as exc:
        return None, "openclaw cron list 执行失败：{}".format(exc)
    if out.returncode != 0:
        return None, "openclaw cron list 退出码 {}：{}".format(
            out.returncode, (out.stderr or "")[:200])
    # 输出是定宽列。按表头里 Name / Schedule 的列位切片——
    # 直接 split() 会把带空格的任务名（"Agent Eden reconcile"）切成第一个词，
    # 于是报告里的名单看起来像一堆残词。
    lines = out.stdout.splitlines()
    lo = hi = None
    for line in lines:
        if line.startswith("ID ") and "Name" in line and "Schedule" in line:
            lo, hi = line.index("Name"), line.index("Schedule")
            break
    names = []
    for line in lines:
        parts = line.split()
        # 表头行与 config warnings 行都不是任务行；任务行首列是 uuid
        if not (len(parts) >= 3 and parts[0].count("-") == 4 and len(parts[0]) == 36):
            continue
        names.append(line[lo:hi].strip() if lo is not None else parts[2])
    return names, None


def wrapper_stems():
    """脚本名 → 把它写死在体内跑的 run_*.sh 执行体名（去掉 run_ 前缀）。

    为什么需要：调度检查拿脚本名去匹配 cron 名，但有的脚本不直接挂 cron，
    而是被某个 run_*.sh 包着挂——buyer_quote_queries.py 就在 run_query_intake.sh
    体内、挂在 `aeo_query_intake` 下。只按脚本名匹配会把「每周都在跑」的链
    误判成「缺调度」（2026-08-07 的报告就这么误报过，当天它明明刚跑完）。

    从 shell 源码里读包装关系而不是手维护一张表：表会随下一个 wrapper 过期。
    run_chain_commit.sh 那种 `scripts/${SCRIPT}.py` 的转发壳不会被匹配到
    （`$`/`{` 不在字符集里），这是刻意的——它对应的 cron 名本身含脚本名，
    直接匹配已覆盖。
    """
    out = {}
    for path in glob.glob(os.path.join(ac.REPO, "scripts", "run_*.sh")):
        stem = os.path.basename(path)[len("run_"):-len(".sh")]
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for name in re.findall(r"scripts/([a-z0-9_]+)\.py", text):
            out.setdefault(name, []).append(stem)
    return out


def claude_task_materials():
    """三个 Claude scheduled task 的材料文件。

    它们建在 Shawn 桌面的 Claude 里，本机查不到调度状态——能核实的只有材料在不在。
    不把「材料在」读成「任务在跑」。
    """
    out = []
    for path in sorted(glob.glob(os.path.join(ac.REPO, "prompts",
                                              "scheduled_task_*.md"))):
        out.append(os.path.relpath(path, ac.REPO))
    return out


def query_db_facts(notion, env):
    rows = notion.query_all(env["DS_QUERY"])
    by_source, by_status, by_type = {}, {}, {}
    vol_known, range_known, source_blank = 0, 0, 0
    for r in rows:
        p = r.get("properties", {})
        s = ac.select_name(p, "数据来源")
        by_source[s or "(空)"] = by_source.get(s or "(空)", 0) + 1
        if not s:
            source_blank += 1
        st = ac.select_name(p, "状态")
        by_status[st or "(空)"] = by_status.get(st or "(空)", 0) + 1
        t = ac.select_name(p, "类型")
        by_type[t or "(空)"] = by_type.get(t or "(空)", 0) + 1
        vp = p.get("月搜索量") or {}
        if vp.get("type") == "number" and vp.get("number") is not None:
            vol_known += 1
        if ac.rich_text(p, "搜索量区间").strip():
            range_known += 1
    created = sorted({r.get("created_time", "")[:10] for r in rows})
    return {
        "total": len(rows),
        "by_数据来源": by_source,
        "by_状态": by_status,
        "by_类型": by_type,
        "月搜索量非空": vol_known,
        "搜索量区间非空": range_known,
        "数据来源为空的行": source_blank,
        "建行日期": created,
    }


def legal_source_values(notion, env):
    """从线上 schema 读 `数据来源` 的合法取值。

    刻意不写死在脚本里：这四个值是 Phase 0 冻结的，冻结的东西以线上为准。
    若哪天线上多出一个值，本脚本会立刻显示出来「有个来源没有对应的链」。
    """
    ds = notion._request("GET", "/data_sources/{}".format(env["DS_QUERY"]))
    prop = (ds.get("properties") or {}).get("数据来源") or {}
    opts = ((prop.get("select") or {}).get("options")) or []
    return [o.get("name") for o in opts]


def winloss_facts(notion, env):
    rows = notion.query_all(env["DS_WINLOSS"])
    with_quote, blank_quote = [], []
    for r in rows:
        p = r.get("properties", {})
        q = ac.rich_text(p, "买家原话").strip()
        item = {"公司": ac.rich_text(p, "公司"),
                "结果": ac.select_name(p, "结果"),
                "page_id": r["id"]}
        (with_quote if q else blank_quote).append(
            dict(item, 买家原话字数=len(q)) if q else item)
    return {"total": len(rows), "有买家原话": len(with_quote),
            "买家原话为空": len(blank_quote),
            "有原话的行": with_quote, "无原话的行": blank_quote}


def kw_csv_facts():
    d = os.path.join(ac.REPO, "data", "kw")
    files = sorted(glob.glob(os.path.join(d, "*.csv")) +
                   glob.glob(os.path.join(d, "*.tsv")))
    return {"dir": os.path.relpath(d, ac.REPO),
            "file_count": len(files),
            "files": [os.path.basename(f) for f in files]}


def candidate_pool_facts():
    """AI 建议候选池现状。文件不存在 = 追问环节还没产出过，不是错误。"""
    path = os.path.join(ac.DATA_DIR, "query_candidates.jsonl")
    if not os.path.exists(path):
        return {"total": 0, "committed": 0, "exists": False}
    total = committed = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            total += 1
            if '"committed_on"' in line:
                committed += 1
    return {"total": total - committed, "committed": committed, "exists": True}


def pipeline_a1_count(notion, env):
    """水箱里来自 A1 扫描的行数——A1 扫描链的燃料。"""
    return sum(1 for r in notion.query_all(env["DS_PIPELINE"])
               if ac.select_name(r.get("properties", {}), "来源") == "A1 扫描")


def serp_writable(notion, env):
    """SERP 链就算有 key，今天能落在 Notion 上的行数。

    serp_scan 只在 `数据来源` **为空**时写标注（不覆盖 Keyword Planner 的来源）。
    Query 库现有行的来源全部非空时，这个数是 0——
    也就是说缺凭据只是它的第一道断点，不是唯一一道。
    """
    rows = notion.query_all(env["DS_QUERY"])
    return sum(1 for r in rows
               if not ac.select_name(r.get("properties", {}), "数据来源"))


# ---------------------------------------------------------------------------
# 逐链判定
# ---------------------------------------------------------------------------

def diagnose(chain, facts, env, crons, wrappers):
    """返回该链的判定。每一条结论都要指得出是哪个事实支撑的。"""
    breaks = []          # (类别, 说明)
    human = []           # 需要真人做的事
    d = {"数据来源": chain["数据来源"], "writer": chain["writer"],
         "kind": chain["kind"], "kind_why": chain["kind_why"],
         "input": chain["input_desc"]}

    # ① 脚本在不在
    if not chain["writer"]:
        breaks.append(("缺脚本", "没有任何脚本写 `{}`——这条链不存在"
                                .format(chain["数据来源"])))
        d["脚本存在"] = False
    else:
        exists = os.path.exists(os.path.join(ac.REPO, chain["writer"]))
        d["脚本存在"] = exists
        if not exists:
            breaks.append(("缺脚本", "{} 不存在".format(chain["writer"])))

    # ② 结构上能不能建行
    d["能建行"] = chain["creates_rows"]
    if chain["creates_rows"] is False:
        breaks.append(("缺 schema", "脚本结构上只 update 不 create——"
                                    "原因是 Query 库 7 个冻结字段装不下它的产出"
                                    "（占位者名单与 URL 列表），所以它无行可建"))
        human.append("Phase 2 §八① 第一个待拍板问题：加字段 / 另建库 / 就留日志")

    # ③ 凭据
    missing = [k for k in chain["credentials"] if not env.get(k)]
    d["缺凭据"] = missing
    if missing:
        breaks.append(("缺凭据", "缺 {}".format(" / ".join(missing))))
        human.append("补凭据：{}".format(" / ".join(missing)))

    # ④ 调度。匹配键 = 脚本名 + 包着它的 run_*.sh 执行体名——
    # 只按脚本名匹配会漏掉包装挂载（见 wrapper_stems 的注释）。
    scheduled = None
    if chain["writer"]:
        stem = os.path.basename(chain["writer"])[:-3]
        keys = [stem] + wrappers.get(stem, [])
        if crons is None:
            scheduled = None
        else:
            scheduled = [c for c in crons if any(k in c for k in keys)]
    d["定时任务"] = ("查不到（openclaw cron list 不可用）" if scheduled is None
                     else scheduled or [])
    if chain["writer"] and scheduled == []:
        breaks.append(("缺调度", "本机 OpenClaw 无任何定时任务跑 {}"
                                "（含 run_*.sh 包装）——"
                                "它只在有人手动敲命令时才跑".format(chain["writer"])))

    # ⑤ 燃料
    if chain["input_kind"] == "config":
        n_cfg = facts["scan_probe_question_count"]
        n_in = facts["query_db"]["by_数据来源"].get(chain["数据来源"], 0)
        d["燃料"] = "scan.yaml 有 {} 条问题，库里已有 {} 条".format(n_cfg, n_in)
        if n_in >= n_cfg:
            breaks.append(("缺真人动作", "config 里的 {} 条问题已全部入库，"
                                        "再跑 to_create=0。要产新词只能由真人先往 "
                                        "scan.yaml 加问题".format(n_cfg)))
            human.append("往 config/scan.yaml 的 probe.questions 加新问题并重新标 approved")
    elif chain["input_kind"] == "csv":
        kw = facts["kw_csv"]
        d["燃料"] = "{} 下有 {} 个 CSV/TSV".format(kw["dir"], kw["file_count"])
        if kw["file_count"] == 0:
            breaks.append(("缺真人动作", "data/kw/ 是空的。脚本能跑，但 parsed_rows=0，"
                                        "一行都建不出来"))
            human.append("从 Keyword Planner 导出 CSV 放 data/kw/"
                         "（要发现新词就连扩展建议一起导，不要只导那 28 条种子）")
    elif chain["input_kind"] == "winloss":
        wl = facts["winloss"]
        d["燃料"] = "win/loss {} 行，其中有「买家原话」的 {} 行".format(
            wl["total"], wl["有买家原话"])
        if wl["有买家原话"] == 0:
            breaks.append(("缺真人动作", "win/loss {} 行里「买家原话」**一条都没有**。"
                                        "这条链的燃料不是 win/loss 行数，是有原话的行数——"
                                        "现在是 0".format(wl["total"])))
            human.append("对话后 24 小时内把「买家原话」逐字填进 win/loss 库"
                         "（零回复的 cold outbound 天然没有原话，攒再多也不产燃料）")
    elif chain["input_kind"] == "pool":
        n = facts["candidate_pool"]
        d["燃料"] = "候选池 {} 条，其中已写进 Query 库 {} 条".format(
            n["total"], n["committed"])
        if n["total"] == 0:
            breaks.append(("缺真人动作", "候选池是空的。追问环节写在 playbook §11，"
                                        "要等每日探测真的跑起来才有料"
                                        "（2026-08-07 起探测只跑 ChatGPT/Gemini 两引擎，"
                                        "不再被 Perplexity 登录态卡死）"))
            human.append("确认桌面端 probe_daily 任务已换成两引擎版材料并正常出跑，"
                         "候选池会随每天 4 次追问自然进料")
    elif chain["input_kind"] == "gsc":
        d["燃料"] = "Search Console 的 16 个月存量（2026-08-05 人工确认："
        d["燃料"] += "总 11.6K 点击 / 149K 曝光，非品牌 200 点击 / 13.7K 曝光）"
        # 燃料是有的，卡的是凭据——上面 ③ 已经把它记进 breaks 了，这里不重复记。
    elif chain["input_kind"] == "pipeline":
        n = facts["pipeline_a1"]
        d["燃料"] = "水箱 `来源 = A1 扫描` 的行 {} 条".format(n)
        if n == 0:
            breaks.append(("缺真人动作", "水箱里 `来源 = A1 扫描` 的行是 0 条——"
                                        "Reddit / LinkedIn 周批扫还没产出过任何一行"))
            human.append("跑首轮周批扫（Reddit 存量提问帖），让 A1 扫描侧真的有料")
    elif chain["input_kind"] == "query_db":
        d["燃料"] = "Query 库 {} 行，其中 `数据来源` 为空、本链可写的 {} 行".format(
            facts["query_db"]["total"], facts["serp_writable"])
        if facts["serp_writable"] == 0:
            breaks.append(("缺 schema", "现有 {} 行的 `数据来源` 全部非空，"
                                        "而本脚本只在该字段为空时写标注（不覆盖别的来源）。"
                                        "即使补上 SERPAPI_KEY，今天它在 Notion 上也是 0 行可写"
                                        .format(facts["query_db"]["total"])))

    d["断点"] = [{"类别": k, "说明": v} for k, v in breaks]
    d["需要真人"] = human
    d["能产新 query"] = (not breaks) and bool(chain["creates_rows"])
    return d


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

BREAK_ORDER = ["缺脚本", "缺 schema", "缺凭据", "缺调度", "缺真人动作"]


def render(result):
    q = result["query_db"]
    L = ["🔎 AEO · Query 库进料链诊断 {}".format(result["generated_at"][:16]),
         "",
         "**Query 库现状**：{} 行 · 来源 `{}` · 状态 `{}`".format(
             q["total"],
             json.dumps(q["by_数据来源"], ensure_ascii=False),
             json.dumps(q["by_状态"], ensure_ascii=False)),
         "量证据：精确值 {} 条 · 区间 {} 条 · 两者皆无 {} 条".format(
             q["月搜索量非空"], q["搜索量区间非空"],
             q["total"] - q["月搜索量非空"] - q["搜索量区间非空"]),
         "建行日期：{}（此后无新行）".format(", ".join(q["建行日期"]) or "无"),
         ""]

    if result["sources_without_chain"]:
        L += ["⚠️ 线上 `数据来源` 有取值没有对应的进料链：{}".format(
            ", ".join(result["sources_without_chain"])), ""]

    L += ["## {} 条链".format(len(result["chains"])), "",
          "| 数据来源 | 能产新 query | 断在哪 | 修通需要什么 | 需真人决定 |",
          "|---|---|---|---|---|"]
    for c in result["chains"]:
        cats = " + ".join(dict.fromkeys(b["类别"] for b in c["断点"])) or "—"
        fix = "；".join(b["说明"] for b in c["断点"]) or "已通"
        need = "是" if c["需要真人"] else "否"
        L.append("| {} | {} | {} | {} | {} |".format(
            c["数据来源"], "✅" if c["能产新 query"] else "❌", cats,
            fix[:300], need))
    L.append("")

    L += ["## 补量 vs 发现", ""]
    for c in result["chains"]:
        L += ["**{}** —— {}".format(c["数据来源"], c["kind"]),
              "", "> {}".format(c["kind_why"]), ""]

    L += ["## 断点按类别汇总", "",
          "五种「断」的修法完全不同，这里分开列——混在一起谈只会得出「都做一遍」。", ""]
    buckets = {}
    for c in result["chains"]:
        for b in c["断点"]:
            buckets.setdefault(b["类别"], []).append(
                "{}：{}".format(c["数据来源"], b["说明"]))
    for cat in BREAK_ORDER:
        if cat in buckets:
            L.append("- **{}**".format(cat))
            for item in buckets[cat]:
                L.append("  - {}".format(item))
    L.append("")

    wl = result["winloss"]
    L += ["## 买家原话链的真实燃料", "",
          "- win/loss 总行数 **{}**（触发线 5）".format(wl["total"]),
          "- 其中「买家原话」非空 **{}** 行".format(wl["有买家原话"]), ""]
    for r in wl["无原话的行"]:
        L.append("  - 无原话：{}（{}）".format(r["公司"] or "?", r["结果"] or "?"))
    L += ["",
          "> 这条链的触发线不该是 win/loss 行数。零回复的 cold outbound 也是一行 loss，",
          "> 但它产出 0 条原话。攒到 5 行仍可能是 0 条原话——**判据要换成有原话的行数**。",
          ""]

    L += ["## 调度现状", "",
          "- 本机 OpenClaw 定时任务：{}".format(
              ", ".join(result["cron_names"]) if result["cron_names"]
              else "⚠️ " + (result["cron_error"] or "无")),
          "- 三个 Claude scheduled task 的材料文件：{}".format(
              ", ".join(result["claude_task_materials"]) or "无"),
          "- 注：Claude 侧任务建在真人桌面的 Claude 里，本机查不到运行状态；"
          "材料文件在 ≠ 任务在跑。且它们只能写 outbox，靠 `outbox_sweep` 兜底转发。",
          ""]
    # 结论按当次数据算，不写死——写死的那句（"四条进料链里没有一条挂着定时任务"）
    # 在链数变成 7、调度陆续挂上之后还在报里原样出现，和上面的表自相矛盾。
    unsched = [c["数据来源"] for c in result["chains"]
               if isinstance(c["定时任务"], list) and not c["定时任务"]]
    if result["cron_names"] is None:
        L.append("cron 名单本次取不到，不下「谁没挂调度」的结论。")
    elif unsched:
        L.append("{} 条链里 **{} 条**的写库脚本没有任何定时任务：{}。".format(
            len(result["chains"]), len(unsched), "、".join(unsched)))
    else:
        L.append("{} 条链的写库脚本全部有调度覆盖（含经 run_*.sh 包装挂载的）。".format(
            len(result["chains"])))
    L.append("")
    return "\n".join(L)


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    args = parser.parse_args()
    mode = sc.resolve_mode(args)
    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        scan_cfg = ac.load_config("scan.yaml")

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])

        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        crons, cron_err = cron_names()
        wrappers = wrapper_stems()

        probe_q = scan_cfg.get("probe", {}).get("questions") or {}
        pool = candidate_pool_facts()
        facts = {
            "query_db": query_db_facts(notion, env),
            "winloss": winloss_facts(notion, env),
            "kw_csv": kw_csv_facts(),
            "serp_writable": serp_writable(notion, env),
            "scan_probe_question_count": sum(len(v or []) for v in probe_q.values()),
            "candidate_pool": pool,
            "pipeline_a1": pipeline_a1_count(notion, env),
        }

        legal = legal_source_values(notion, env)
        covered = {c["数据来源"] for c in CHAINS}

        result = {
            "script": SCRIPT,
            "mode": mode,
            "wrote_notion": False,
            "generated_at": datetime.now(tz).isoformat(),
            "query_db": facts["query_db"],
            "winloss": facts["winloss"],
            "kw_csv": facts["kw_csv"],
            "serp_writable_rows": facts["serp_writable"],
            "legal_数据来源_options": legal,
            "sources_without_chain": [s for s in legal if s not in covered],
            "cron_names": crons,
            "cron_error": cron_err,
            "claude_task_materials": claude_task_materials(),
            "chains": [diagnose(c, facts, env, crons, wrappers) for c in CHAINS],
        }
        body = render(result)
        result["report"] = body
        sc.emit(SCRIPT, result, th)
        print(body, file=sys.stderr)

        if mode == "commit":
            path = ac.write_outbox(
                "query_intake_health_{}.md".format(
                    datetime.now(tz).strftime("%Y-%m-%d")), body)
            print("PUSH: {}".format(path))
        return sc.EXIT_OK
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
