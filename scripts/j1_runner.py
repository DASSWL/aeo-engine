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

import hashlib
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


REFUSED_FILE = os.path.join(ac.REPO, "data", "j1_refused.jsonl")

# 闸门分组指纹。2026-08-17 Shawn 拍板:「如果 keyword 短时间内没变过,
# 就不要每天去验证了」。见 gate_state / filtered_report 的注释。
GATE_STATE_FILE = os.path.join(ac.REPO, "data", "j1_filter_state.json")


def load_refused():
    """读被闸门拒过的选题 → {归一 query: 最后一次拒绝记录}。

    2026-08-12 新增。此前 REFUSE 只写进 logs/j1_assemble_*.json 的 failed 字段,
    而 pick_queries 只按台账行去重、REFUSE 不产生台账行——于是被拒的选题
    下一轮还会被选中,永远。08-12 那两条本来会每个周三原样再来一遍,
    每次烧一次 opus 调用,产出恒为 0。

    刻意落本机 jsonl 而不是 Notion:台账只放真资产,被拒的不是资产。
    单行 JSON 是为了人可读——真人想复活某条,删掉那一行就行。
    """
    out = {}
    if not os.path.exists(REFUSED_FILE):
        return out
    with open(REFUSED_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue        # 手改坏的行不该让整条产线停摆,跳过即可
            key = sc.norm_query(rec.get("query") or "")
            if key:
                out[key] = rec
    return out


def append_refused(records):
    """把本轮被拒的选题追加进 REFUSED_FILE。已在文件里的不重复追加。"""
    if not records:
        return []
    known = load_refused()
    fresh = [r for r in records if sc.norm_query(r["query"]) not in known]
    if not fresh:
        return []
    os.makedirs(os.path.dirname(REFUSED_FILE), exist_ok=True)
    with open(REFUSED_FILE, "a", encoding="utf-8") as fh:
        for r in fresh:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return fresh


def pick_queries(queries, ledger, cfg, refused=None):
    """选题队列。返回 (选中, 逐条落选原因, 完整排序队列)。

    排除已有台账行的 query(按 sc.norm_query 归一后比对台账「面向」)——
    重复登记同一选题会让签发队列出现两行指同一篇。

    2026-08-12 两处改动(Shawn 拍板「被闸门限制住的都要让我知道」):
    ① 每一次落选都留名。原来「类型不在范围」与「数据来源不在白名单」两条是
       静默 continue(注释写的是「连落选都不算,不噪音」)。代价是库里绝大多数行
       被过滤掉却不留任何痕迹——当前 116 行里有 93 行是这么消失的,
       其中光 Keyword Planner 的痛点级就有 32 条。看不见的过滤没法审。
    ② 排除 REFUSED_FILE 里的选题,见 load_refused()。
    """
    q = cfg["queue"]
    refused = load_refused() if refused is None else refused
    ledger_facing = {sc.norm_query(ac.rich_text(r.get("properties", {}), "面向"))
                     for r in ledger}

    picked, skipped = [], []
    for row in queries:
        p = row.get("properties", {})
        text = ac.title_text(p, "query 文本")
        qtype = ac.select_name(p, "类型")
        source = ac.select_name(p, "数据来源")
        status = ac.select_name(p, "状态")
        key = sc.norm_query(text)
        if qtype not in q["types_allowed"]:
            skipped.append({"query": text, "gate": "类型白名单",
                            "reason": "类型={}(仅收 {})".format(
                                qtype, "/".join(q["types_allowed"]))})
            continue
        if source not in q["sources_allowed"]:
            skipped.append({"query": text, "gate": "来源白名单",
                            "reason": "数据来源={}(不在白名单)".format(source)})
            continue
        if status in q["statuses_exclude"]:
            skipped.append({"query": text, "gate": "状态黑名单",
                            "reason": "状态={}".format(status)})
            continue
        if key in ledger_facing:
            skipped.append({"query": text, "gate": "台账去重",
                            "reason": "台账已有同选题行"})
            continue
        if key in refused:
            r = refused[key]
            skipped.append({"query": text, "gate": "此前被闸门拒过",
                            "reason": "{} 被拒:{}".format(
                                r.get("date", "?"), (r.get("reason") or "")[:160]),
                            "revive_hint": "要复活它:删掉 data/j1_refused.jsonl 里对应那一行"})
            continue
        picked.append({
            "slug": slugify(text),
            "query": text,
            "row_id": row.get("id"),
            # 2026-08-17 起 types_allowed 含评估式,类型要一路带到 prompt:
            # 两类选题的写法边界不同(评估式不许做竞品裁决),混着写等于没放开。
            "类型": qtype,
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

    # 完整排序队列一并返回并落盘。2026-08-12 新增:
    # 此前 plan JSON 只留了 drafts(前 max_per_run 条),整条排序结果不落任何地方——
    # 「第 3 名到第 23 名长什么样」在仓库里查不到,只能临时跑脚本重算。
    # 排序口径本身是要审的东西(短头词与长尾问句的量不可比),看不到就审不了。
    ranked = [{"rank": i, "query": it["query"], "月搜索量": it["月搜索量"],
               "数据来源": it["数据来源"], "类型": it["类型"],
               "picked": i <= q["max_per_run"]}
              for i, it in enumerate(picked, 1)]

    return picked[:q["max_per_run"]], skipped, ranked


def gate_state(path=GATE_STATE_FILE):
    """读闸门分组指纹。文件不存在/坏了 → 空字典(退回逐条展开,不因缓存崩掉报告)。"""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def gate_fingerprint(rows):
    """一组落选行 → (指纹, 归一后的 query 列表)。

    指纹只认「哪些 query 被这道闸挡住」,不认顺序、不认 reason 文案:
    同一批词换了个顺序不该被当成变化,reason 里的白名单串改了排版也不该。
    """
    keys = sorted({sc.norm_query(r["query"]) for r in rows})
    fp = hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:12]
    return fp, keys


def filtered_report(ranked, skipped, now, cfg, state_path=GATE_STATE_FILE):
    """过滤报告正文。返回 (正文, 新的闸门指纹状态, 本轮被折叠的闸门名)。

    Shawn 2026-08-12 拍板:被闸门挡掉的东西必须能看见。

    每轮固定产一份(队列为空也产)。分两段:
      ① 完整排序队列——第几名、多少量、来自哪条链、这轮取没取
      ② 逐条落选,按闸门分组——哪一道闸挡的、为什么
    合计数必须对得上库里的行数,对不上说明还有一道没留名的闸。

    2026-08-17 Shawn 拍板「keyword 短时间内没变过就不要每天去验证了」:
    与上一轮**完全相同**的闸门分组折叠成一行,只留档数、起始日期和上次展开的文件名。

    ⚠️ 折叠的是**展示**,不是**判定**:每一条仍然逐条过闸、仍然计入合计数,
    只是不再把同一份 69 行清单重印一遍。这个区分是刻意的——
    「不再验证」的字面实现是把 KP 词放进队列,那会拆掉 08-06 立起来的那道闸。
    真要不验,改 j1.yaml 的 sources_allowed,那是另一个决定。
    """
    q = cfg["queue"]
    L = ["# J1 选题过滤报告 · {}".format(now.strftime("%Y-%m-%d")), "",
         "本轮取前 **{}** 条(`j1.yaml: queue.max_per_run`)。"
         "候选 {} 条、落选 {} 条。".format(q["max_per_run"], len(ranked), len(skipped)), ""]

    L += ["## 一、排序队列(完整)", ""]
    if not ranked:
        L += ["候选队列为空——所有行都被下面的闸挡住了。", ""]
    else:
        L += ["| # | 取 | 月搜索量 | 数据来源 | query |", "|--:|:--:|--:|---|---|"]
        for r in ranked:
            L.append("| {} | {} | {} | {} | {} |".format(
                r["rank"], "✅" if r["picked"] else "",
                r["月搜索量"] if r["月搜索量"] is not None else "—",
                r["数据来源"], r["query"]))
        L += ["",
              "> 排序口径:有量优先、量降序,无量的按原序排在最后"
              "(`j1_runner.pick_queries`)。",
              "> ⚠️ 短头词与长尾问句的量不可比——前者是宽泛品类词的总量,"
              "后者是一个具体问法。同一把尺子排,宽泛词永远在前。", ""]

    L += ["## 二、被闸门挡掉的({} 条)".format(len(skipped)), ""]
    today = now.strftime("%Y-%m-%d")
    this_file = "outbox/j1_filtered_{}.md".format(today)
    state = gate_state(state_path)
    new_state, folded = {}, []
    if not skipped:
        L += ["无。", ""]
    else:
        by_gate = {}
        for row in skipped:
            by_gate.setdefault(row.get("gate", "未标注"), []).append(row)
        for gate in sorted(by_gate, key=lambda g: -len(by_gate[g])):
            rows = by_gate[gate]
            fp, keys = gate_fingerprint(rows)
            prev = state.get(gate) or {}
            unchanged = prev.get("fingerprint") == fp
            if unchanged:
                # 折叠。这一档一个字没变,逐条展开等于每天让人重读同一份 69 行清单——
                # 天天重复的东西没人会读,真正变了的那天也就没人看得见。
                folded.append(gate)
                L += ["### {} —— {} 条(自 {} 起每轮完全相同,已折叠)".format(
                          gate, len(rows), prev.get("since") or "?"), "",
                      "> 这一档的清单**一个字没变**。全清单见最后一次展开:"
                      "`{}`。".format(prev.get("listed_in") or "(无记录)"),
                      "> 变一个词就自动展开,新增的会标 🆕。"
                      "指纹存在 `data/j1_filter_state.json`,删掉即强制重新展开。", ""]
                new_state[gate] = prev
            else:
                before = set(prev.get("keys") or [])
                fresh = [r for r in rows if sc.norm_query(r["query"]) not in before]
                head = "### {} —— {} 条".format(gate, len(rows))
                if prev:
                    head += "(上轮 {} 条,新增 {} 条)".format(len(before), len(fresh))
                L += [head, ""]
                fresh_keys = {sc.norm_query(r["query"]) for r in fresh}
                for row in rows:
                    mark = "🆕 " if sc.norm_query(row["query"]) in fresh_keys else ""
                    L.append("- {}`{}` —— {}".format(mark, row["query"], row["reason"]))
                    if row.get("revive_hint"):
                        L.append("  - {}".format(row["revive_hint"]))
                L.append("")
                # since = 本轮日期:这一档从今天起是这个样子。折叠时原样带走,
                # 于是折叠行里的「自 X 起相同」说的是「上次变化发生在 X」。
                new_state[gate] = {"fingerprint": fp, "keys": keys,
                                   "count": len(rows), "since": today,
                                   "listed_in": this_file}

    if folded:
        L += ["> 本轮折叠了 {} 档:{}。".format(len(folded), "、".join(folded)),
              "> 折叠的口径是「与上一轮**完全相同**」,不是抽样也不是超时——"
              "只要有一条进出就会展开。", ""]

    L += ["---", "",
          "闸门挡掉不等于判错——挡对了也要看得见,否则没人能审这道闸。",
          "要改哪一道:类型/来源白名单与状态黑名单在 `config/j1.yaml: queue`;",
          "「此前被闸门拒过」在 `data/j1_refused.jsonl`(删掉对应行即复活)。"]
    return "\n".join(L) + "\n", new_state, folded


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


def probe_candidates(probe_rows, cfg):
    """降级证据 · 第二档:探测记录库 → PRB- 编号 + 引擎回答摘录。

    ⚠️ 这**不是**买家痛点。它是引擎生成的内容,说明的是「现在搜这个问题的人
    会看到什么答案」,即这篇文章要压过谁。把它当买家的话用,等于拿 ChatGPT
    对买家的猜测冒充买家本人;把里面的产品描述当事实用,等于把引擎的幻觉
    洗进 vivu.ai 的正文。档位语义一路传到 prompt 与台账,见 j1.yaml 的注释。
    """
    import hashlib
    fb = (cfg["evidence"].get("fallback") or {})
    if not fb.get("enabled"):
        return []
    pc = fb.get("probe") or {}
    engines = pc.get("engines") or []

    out, seen = [], set()
    for row in probe_rows:
        p = row.get("properties", {})
        engine = ac.select_name(p, "引擎")
        question = ac.rich_text(p, "具体问题").strip()
        excerpt = ac.rich_text(p, "回答摘录").strip()
        date = ((p.get("日期") or {}).get("date") or {}).get("start") or ""
        if not question or not excerpt:
            continue
        if engines and engine not in engines:
            continue
        # 去重键 = 日期|引擎|问题,与 j1_evidence.resolve_evidence 逐字一致。
        # 编号必须能从行本身重算出来,否则解析不回去——这与 SIG 同一口径。
        key = "{}|{}|{}".format(date[:10], engine, sc.norm_query(question))
        code = "PRB-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "row_id": row.get("id"),
                    "engine": engine, "question": question,
                    "被引用竞品": ac.rich_text(p, "被引用竞品"),
                    "品类答案是否形成": ac.select_name(p, "品类答案是否形成"),
                    "quote": excerpt[:pc.get("excerpt_max_chars", 260)]})
    return out[:pc.get("max_candidates_in_prompt", 8)]


def keyword_candidates(queries, cfg):
    """降级证据 · 第三档:Query 库有量的词 → KW- 编号 + 月搜索量。

    ⚠️ 这只是一个**检索需求信号**:有人搜这个词。它**不说明他为什么搜、
    也不说明他难在哪**。拿它当痛点写,写出来的痛点是编的。
    """
    import hashlib
    fb = (cfg["evidence"].get("fallback") or {})
    if not fb.get("enabled"):
        return []
    kc = fb.get("keyword") or {}

    out, seen = [], set()
    for row in queries:
        p = row.get("properties", {})
        text = ac.title_text(p, "query 文本").strip()
        vol = (p.get("月搜索量") or {}).get("number")
        rng = ac.rich_text(p, "搜索量区间").strip()
        if not text:
            continue
        if kc.get("require_volume", True) and vol is None and not rng:
            continue
        code = "KW-" + hashlib.sha1(
            sc.norm_query(text).encode("utf-8")).hexdigest()[:8]
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "row_id": row.get("id"), "query": text,
                    "月搜索量": vol, "搜索量区间": rng,
                    "数据来源": ac.select_name(p, "数据来源")})
    out.sort(key=lambda r: -(r["月搜索量"] or 0))
    return out[:kc.get("max_candidates_in_prompt", 8)]


def confirmed_facts(cfg):
    """facts.json 里 status=已确认 的字段 → prompt 用的事实清单。

    只认「已确认」。待真人补的字段视同不存在——那些字段的值是 null,
    facts.json 自己的 lint 也保证它们没有值,拿不出东西可写。

    ⚠️ 2026-08-13 起这份清单是**基准与参考**,不再是能力描述的天花板
    (Shawn 拍板,见 config/j1.yaml facts_policy)。它仍然是站点与 AEO 页的
    对齐点,而 negatives 那几条是真正的硬边界:数字类只能来自这里,
    这里是 null 就一个字都不写。
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


def facts_policy_block(cfg):
    """fact boundary → prompt 的一节。

    出处:config/j1.yaml facts_policy(Shawn 2026-08-13 拍板「能力描述全放开,
    只锁死数字类」)。上面那份 facts.json 清单是参考,**这一节才是边界**,
    所以它必须紧跟在清单后面、在 negatives 之前——中间隔开会让执行体
    继续把清单当天花板读。

    配置缺这一节时返回空:老配置退回原口径(清单即上限),不因为加了个字段就崩。
    """
    fp = cfg.get("facts_policy")
    if not fp:
        return []
    lines = [
        "### 这份清单是参考,不是天花板",
        "",
        "**能力类描述**(产品能做什么、怎么接入、边界在哪):",
        "",
    ]
    lines += ["- {}".format(r) for r in fp.get("capability_claims", [])]
    lines += [
        "",
        "**下面这几类锁死**,只能来自上面的清单;上面是空的就一个字都不写。"
        "它们的共同点是:错了读者靠常识发现不了,而且直接影响商业判断。",
        "",
    ]
    lines += ["- 🔒 {}".format(r) for r in fp.get("locked", [])]
    lines += [""]
    return lines


def vivu_mention_block(art):
    """Vivu 在正文里怎么出现 → prompt 的一节。

    出处:config/j1.yaml article.vivu_mention（Shawn 2026-08-13 拍板）。
    业务值全在配置里,这里只负责把它讲成一条执行体读得懂的规则,
    并把「为什么」一起递过去——只给禁令不给理由,执行体会换个标题接着犯。

    配置缺这一节时返回空:老配置照跑,不因为加了个字段就崩。
    """
    vm = art.get("vivu_mention")
    if not vm:
        return []
    return [
        "## Vivu 怎么出现在正文里(这一节和事实边界同等重要)",
        "",
        "这条产线已经发出去的每一篇,都长出了一段独立的 Vivu 小节,"
        "内容是同一份产品事实清单——标题换了四个写法,清单一字不改,连顺序都一样。"
        "那不是文章的一部分,是贴在文章上的产品说明书:读者一眼认出是广告就跳过,"
        "answer engine 也拿不到任何这一篇独有的东西。",
        "",
        "所以这一篇里,Vivu **最多出现 {} 句**(结尾那句固定 CTA 不计入),"
        "并且:".format(vm["max_sentences"]),
        "",
    ] + ["- {}".format(r) for r in vm.get("rules", [])] + [
        "",
        "> 自检:把这几句单独抄出来,问一句「换成上一篇的题目,它还成立吗?」"
        "成立就说明它写的是 Vivu 而不是这一篇的问题,重写。",
        "",
    ]


def build_prompt(items, candidates, cfg, skill_report,
                 probe_cands=None, kw_cands=None):
    fact_lines, negatives = confirmed_facts(cfg)
    art = cfg["article"]
    missing = skill_report.get("missing_required") or []

    lines = [
        "# 任务:为 Vivu 写 AEO 内容(Query 库选题的回答文章)",
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
        "## 产品事实(基准与参考)",
        "",
        "以下来自站点事实层 facts.json,只含已确认字段:",
        "",
        "⚠️ 这是**参考,不是清单**。它划定站点现在的对外口径,"
        "不表示「这些话你都要说」。把它整份倒进文章是这条产线出过的原病"
        "(见下面「Vivu 怎么出现在正文里」)。",
        "",
    ]
    lines += fact_lines
    lines += [""]
    lines += facts_policy_block(cfg)
    for n in negatives:
        lines.append("- ⛔ {}".format(n))
    lines += [
        "",
        "## 证据 · 第一档 SIG(真实买家原话,唯一能校准痛点形态的东西)",
        "",
        "每条是水箱里一行真实信号的原话摘录。规则:",
        "- 每篇文章从下面挑 1-3 条与该 query 痛点形态吻合的证据,"
        "在 EVIDENCE 行写出它们的编号。",
        "- 证据只用来校准痛点写法。**正文不许出现当事人身份、公司名或原话直引**。",
        "- **优先用这一档。** 只有这一档一条都配不上时,才往下看第二、三档。",
        "",
    ]

    for c in candidates:
        lines.append("- {}: {}".format(c["code"], c["quote"]))

    if probe_cands or kw_cands:
        lines += [
            "",
            "## 降级证据(**只在第一档一条都配不上时才用**)",
            "",
            "⚠️ **下面两档不是买家说的话。** 用它们写出来的文章,痛点形态没有真人"
            "证据背书 —— 这是一次有代价的降级,不是等价替换。所以:",
            "- 能用第一档就绝不用这两档;混用时 EVIDENCE 行把用到的编号都写上。",
            "- 只用了这两档的文章,**不许写任何关于「买家感受 / 买家处境」的断言**"
            "(`teams struggle with…`、`editors waste hours…` 这类)。"
            "你没有任何证据支持那句话。",
            "- 写法改成描述性的:这个问题存在、现在的答案长什么样、缺了哪一块。",
            "",
        ]
    if probe_cands:
        lines += [
            "### 第二档 PRB —— AI 引擎现在怎么答这个问题",
            "",
            "这是 ChatGPT / Gemini **生成的内容**,说明的是「今天搜这个问题的人"
            "会看到什么答案」,也就是这篇文章要压过谁。",
            "",
            "- ⛔ **不是买家痛点。** 引擎对买家的猜测不等于买家的话。",
            "- ⛔ **不是事实来源。** 里面出现的任何产品能力、价格、数字一律不许引用"
            " —— 正文里的产品事实只能来自上面的 facts.json。引擎会一本正经地编。",
            "- ✅ 可以用来:判断这个问题的答案空间长什么样、哪些工具已经占了位、"
            "现有答案缺了哪一块。",
            "",
        ]
        for c in probe_cands:
            lines.append("- {} [{}｜品类答案:{}] Q: {} ‖ A: {}".format(
                c["code"], c["engine"], c["品类答案是否形成"] or "?",
                c["question"], c["quote"]))
        lines.append("")
    if kw_cands:
        lines += [
            "### 第三档 KW —— 有人搜这个词(只是需求信号)",
            "",
            "- ⛔ 它**不说明**这些人为什么搜、也不说明他们难在哪。"
            "拿它当痛点写,写出来的痛点是编的。",
            "- ✅ 可以用来:确认这个问法真的有人在搜、相邻的问法有哪些。",
            "",
        ]
        for c in kw_cands:
            lines.append("- {} `{}` — 月搜索量 {}{}".format(
                c["code"], c["query"],
                c["月搜索量"] if c["月搜索量"] is not None else "无",
                "(区间 {})".format(c["搜索量区间"]) if c["搜索量区间"] else ""))
        lines.append("")

    lines += [
        "> 三档都配不上的 query 才 REFUSE。没有任何证据的内容只能靠编,"
        "而编造正是这条产线的闸门要防的。",
        "",
    ]

    rp = cfg["refusal_policy"]
    lines += [
        "",
        "## 什么时候可以 REFUSE(只有这几种,其余一律照写)",
        "",
    ]
    lines += ["- {}".format(r) for r in rp["valid_reasons"]]
    lines += [
        "",
        "**以下都不是拒稿理由**:",
        "",
    ]
    lines += ["- ❌ {}".format(r) for r in rp["not_reasons"]]
    lines += [
        "",
        "> 特别说明 `claims.does_not_edit_or_generate`:它是**你要写进正文的一条事实**,"
        "不是你不能写这篇的理由。",
        "> 搜 `ai powered video editor` 的人是真实读者,他要的答案里本来就该包含"
        "「哪些工具做剪辑、哪些不做、Vivu 负责哪一段」。",
        "> 诚实说明 Vivu 不剪辑不生成 —— 那是内容,不是障碍。",
        "> 但正文**仍然不许**说 Vivu 会剪辑或生成:放宽的是写不写,不是能说什么。",
        "",
        "## 文章要求",
        "",
        "- 语言:{}。长度 {} 词。".format(art["language"], art["word_range"]),
        "- H1 即回答:标题直接承接 query 的问法。",
        "- 第一段给出直接答案(answer-engine 会截取它),然后再展开。",
        "- 诚实展开所有路线(手动、DIY、别的工具形态,以及检索层这一类)。"
        "路线是**品类**,不是产品:每条都讲清形态、代价和边界,"
        "包括读者最后不用 Vivu 的那几条。",
        "- 自然的位置放一段「什么情况下你其实不需要这类工具」。",
        "- 结尾:{}".format(art["ending"]),
        "- 标题 sentence case;不用 emoji;少用 em dash。",
        "",
    ]
    lines += vivu_mention_block(art)
    lines += [
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
    if any((it.get("类型") or "") == "评估式" for it in items):
        # 2026-08-17 Shawn 拍板放开评估式选题。放开的是选题,不是竞品裁决权:
        # gates.yaml competitor_list_converged 仍是 false,竞替名单没有从真实对话
        # 里收敛出来。没有这一节,执行体看到「best AI DAM」只会去排名——
        # 那正是 Concept 明令禁止的「凭猜测指定对比页对象」。
        lines[-1:] = [
            "", "> ⚠️ **下面标着「类型: 评估式」的选题有额外边界,见这一段。**", "",
            "### 评估式选题的额外边界", "",
            "这类 query(`best X` / `X alternative` / `哪个更好`)问的是选型。",
            "**但 Vivu 现在没有资格给出选型裁决**:竞替名单还没有从真实对话里"
            "收敛出来(`gates.yaml: competitor_list_converged=false`),",
            "任何「A 比 B 好」的话都只能靠编。所以:", "",
            "- ⛔ **不许排名、不许给竞品打分、不许写「最好的是……」。**",
            "- ⛔ **不许写任何竞品的能力、价格、限制** —— 无论你多确定。"
            "facts.json 只覆盖 Vivu 自己,竞品事实这条产线一个来源都没有。",
            "- ⛔ **不许自己列一张竞品清单。** 只有证据里已经出现的产品名才可以提,"
            "且只能转述证据说了什么。",
            "- ✅ **可以写的是「怎么选」而不是「选谁」**:这类工具的差异出在哪几个维度、"
            "每个维度该问什么问题、什么场景下哪一类做法会失效。"
            "读者拿走的是一把尺子,不是一份榜单。",
            "- ✅ Vivu 自己的能力照常写,边界照旧只认 facts.json。", "",
            "> 做不到上面这些就 REFUSE 这一条 —— 编一份榜单比不写伤害大得多。", "",
        ]
    for it in items:
        vol = it["月搜索量"] if it["月搜索量"] is not None else (it["搜索量区间"] or "无量证据")
        lines += [
            "---",
            "",
            "### slug: {}".format(it["slug"]),
            "",
            "- query: {}".format(it["query"]),
            "- 类型: {}".format(it.get("类型") or "-"),
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


def _chunks(text, size=1900):
    """Notion 单个 rich_text 元素上限 2000 字符,切块。同 draft_runner 的口径。"""
    text = text or ""
    return [{"type": "text", "text": {"content": text[i:i + size]}}
            for i in range(0, max(len(text), 1), size)] or \
           [{"type": "text", "text": {"content": ""}}]


def mirror_article_to_ledger(notion, ledger_row_id, item, draft, outfile, now):
    """把文章正文镜像到台账行的页面正文里。返回一条结果记录,不抛异常。

    Shawn 2026-08-12 在 Notion 台账页上提的两件事:
      「标题和你的标题不一样」—— 台账 title 是 `资产名`(= AEO 内容｜选题),
        **文章标题此前在 Notion 里根本不存在**,只活在 outbox 草稿和已发布页 frontmatter。
      「看不到具体内容,应该去哪里看」—— 正文此前完全不在 Notion。

    做法照抄 J4 的既有形态(draft_runner.mirror_draft_to_page,2026-08-06 落成):
    页面正文追加 = 文章标题 + 路径/链接 + 正文 code block。
    用 code block 是为了 Notion 一键复制,手机上免圈选。

    失败不炸 run:文件已落 outbox、台账行已登记,镜像只是可读性。
    追加后独立回读核对(重列 children 找 heading),不信 append 自己的回执。
    按 heading 去重,assemble 重跑幂等。
    """
    heading = "{}｜{}".format(now.strftime("%Y-%m-%d"), draft["title"])

    def heading_exists():
        kids = notion.list_children(ledger_row_id).get("results", [])
        return any(
            b.get("type") == "heading_3" and heading == "".join(
                t.get("plain_text", "") for t in b["heading_3"].get("rich_text", []))
            for b in kids)

    meta = [
        "选题(面向):  {}".format(item["query"]),
        "证据编号:     {}".format(", ".join(draft["evidence"])),
        "本机草稿:     {}".format(outfile),
        "状态:         草稿——签发 = 把「状态」改成「已签发」,签发日期会自动回填",
    ]
    blocks = [
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": _chunks(heading)}},
        {"object": "block", "type": "code",
         "code": {"language": "plain text", "rich_text": _chunks("\n".join(meta))}},
        {"object": "block", "type": "code",
         "code": {"language": "markdown", "rich_text": _chunks(draft["body"].strip())}},
    ]
    try:
        if heading_exists():
            return {"ledger_row_id": ledger_row_id, "status": "already_mirrored"}
        notion.append_blocks(ledger_row_id, blocks)
        return {"ledger_row_id": ledger_row_id, "status": "mirrored",
                "readback_ok": heading_exists()}
    except Exception as exc:                                   # noqa: BLE001
        return {"ledger_row_id": ledger_row_id, "status": "failed",
                "error": str(exc)[:300]}


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

            # 三档候选一起进校验表——claude 引用了哪一档都要能对上。
            # 旧 plan 文件没有后两个键,用 .get 兜住,不让 assemble 因为读老 plan 而炸。
            all_cands = (plan["evidence_candidates"]
                         + plan.get("probe_candidates", [])
                         + plan.get("keyword_candidates", []))
            cand_codes = {c["code"] for c in all_cands}
            cand_by_code = {c["code"]: c for c in all_cands}
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
                mirror = None
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
                    # 正文镜像进台账行页面。镜像失败不炸 run——文件已落 outbox、
                    # 台账行已登记,这一步只是让人在 Notion 里读得到。
                    if ledger_result.get("ledger_row_id"):
                        # 资产名带上**文章标题**。j1_evidence 建行时只有选题、
                        # 还没有标题(标题是 claude 写完才有的),所以那边只能写
                        # 「AEO 内容｜<选题>」。结果是 Notion 列表视图里每一行
                        # 显示的都是选题,与文章标题对不上——Shawn 2026-08-12
                        # 在台账页上第一句就是「标题和你的标题不一样」。
                        # 选题不丢:它在「面向」列里,且下游匹配一直走那一列。
                        try:
                            notion.update_page(
                                ledger_result["ledger_row_id"],
                                {"资产名": sc.p_title("AEO 内容｜{}".format(d["title"]))})
                        except Exception as exc:               # noqa: BLE001
                            print("TITLE_UPDATE_FAILED {}: {}".format(
                                slug, str(exc)[:200]), file=sys.stderr)
                        mirror = mirror_article_to_ledger(
                            notion, ledger_result["ledger_row_id"], item, d,
                            fpath, now)
                        if mirror["status"] == "failed":
                            print("MIRROR_FAILED {}: {}".format(
                                slug, mirror.get("error")), file=sys.stderr)
                written.append({"slug": slug, "query": item["query"],
                                "title": d["title"], "evidence": d["evidence"],
                                "file": fname,
                                "ledger": ledger_result,
                                "notion_mirror": mirror})

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

            # 被 claude 闸门拒掉的落 data/j1_refused.jsonl,下一轮不再选中。
            # 只落「claude REFUSE」这一类:它是对**选题本身**的判断,重跑还是同样结果。
            # 解析失败 / 引用了表外证据 / 台账登记失败都是**本次运行**的事故,
            # 下次可能就好了——把它们也拉黑等于用一次偶发故障永久废掉一个选题。
            newly_refused = []
            if commit:
                newly_refused = append_refused([
                    {"date": now.strftime("%Y-%m-%d"), "query": f["query"],
                     "slug": f["slug"], "gate": "claude REFUSE",
                     "reason": f["reason"].replace("claude REFUSE:", "", 1)}
                    for f in failed if f["reason"].startswith("claude REFUSE:")])

            # 被拒通知。plan 时的过滤报告产在 claude 跑之前,装不下 claude 的判断,
            # 所以这一份单独产。有 failed 就产,没有就不产(不制造空消息)。
            refused_notify = None
            if commit and failed:
                rname = "j1_refused_{}.md".format(now.strftime("%Y-%m-%d"))
                RL = ["# J1 本轮被拒 · {}".format(now.strftime("%Y-%m-%d")), "",
                      "计划 {} 篇,成稿 {} 篇,被拒 {} 篇。".format(
                          len(plan["drafts"]), len(written), len(failed)), ""]
                for f in failed:
                    permanent = f["reason"].startswith("claude REFUSE:")
                    RL += ["## `{}`".format(f["query"]),
                           "- 判定：{}".format(
                               "**闸门拒稿**（已出列，下轮不再选中）" if permanent
                               else "**本次运行事故**（未出列，下轮还会再试）"),
                           "- 原因：{}".format(f["reason"]), ""]
                RL += ["---", "",
                       "闸门拒稿的选题已写进 `data/j1_refused.jsonl`。",
                       "**判错了要复活它**：删掉该文件里对应那一行即可，下轮自动回到队列。", "",
                       "> 只有「闸门拒稿」才出列——它是对选题本身的判断，重跑还是同样结果。",
                       "> 解析失败 / 引用表外证据 / 台账登记失败属于本次运行的事故，",
                       "> 不出列：用一次偶发故障永久废掉一个选题是不对的。"]
                ac.write_outbox(rname, "\n".join(RL) + "\n")
                refused_notify = os.path.join(ac.OUTBOX_DIR, rname)

            result = {
                "script": SCRIPT, "step": "assemble", "mode": sc.resolve_mode(args),
                "generated_at": now.isoformat(),
                "written": written, "failed": failed, "readback": readback,
                "newly_refused": newly_refused,
                "refused_file": REFUSED_FILE,
                "refused_notify": refused_notify,
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

        picked, skipped, ranked = pick_queries(queries, ledger, cfg)
        if args.limit is not None:
            picked = picked[:args.limit]
        candidates = evidence_candidates(pipeline, cfg)
        # 降级证据两档。空列表也照常往下走——build_prompt 会自己跳过空档,
        # 有几档就说几档,不制造「这里本该有东西」的错觉。
        probe_rows = notion.query_all(env["DS_PROBE"])
        probe_cands = probe_candidates(probe_rows, cfg)
        kw_cands = keyword_candidates(queries, cfg)

        result = {
            "script": SCRIPT, "step": "plan", "mode": sc.resolve_mode(args),
            "generated_at": now.isoformat(),
            "row_counts_read": {"query": len(queries), "ledger": len(ledger),
                                "pipeline": len(pipeline)},
            "drafts": picked, "skipped": skipped,
            "ranked_queue": ranked,
            "evidence_candidates": candidates,
            "probe_candidates": probe_cands,
            "keyword_candidates": kw_cands,
            "skill_report": skill_report,
        }

        if args.emit_prompt:
            # plan 文件只在 production(--emit-prompt)时写,
            # 诊断性 dry-run 不碰它——Phase 3 §七④ 覆写事故的教训。
            with open(plan_path_default, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=2)
            prompt = build_prompt(picked, candidates, cfg, skill_report,
                                  probe_cands, kw_cands)
            with open(args.emit_prompt, "w", encoding="utf-8") as fh:
                fh.write(prompt)
            result["plan_file"] = plan_path_default
            result["prompt_file"] = args.emit_prompt

            # 过滤报告:每轮固定产一份,选题队列为空也产。
            # Shawn 2026-08-12:「以后遇到被闸门限制住的,都给我发一个消息
            # 让我知道什么东西被过滤掉了」。此前这件事完全不可见——
            # 08-12 那轮两条选题全被拒,群里一个字都没有(见 run_j1_draft.sh 注释)。
            filt_path = os.path.join(
                ac.OUTBOX_DIR, "j1_filtered_{}.md".format(now.strftime("%Y-%m-%d")))
            body, gstate, folded = filtered_report(ranked, skipped, now, cfg)
            with open(filt_path, "w", encoding="utf-8") as fh:
                fh.write(body)
            # 指纹状态与报告同生共死:报告写成功了才更新指纹,
            # 否则下一轮会拿一份没人见过的指纹去折叠。
            with open(GATE_STATE_FILE, "w", encoding="utf-8") as fh:
                json.dump(gstate, fh, ensure_ascii=False, indent=2)
            result["filtered_file"] = filt_path
            result["gate_folded"] = folded

        print("DRAFTS_PLANNED: {}".format(len(picked)), file=sys.stderr)
        sc.emit(SCRIPT, result, th)
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
