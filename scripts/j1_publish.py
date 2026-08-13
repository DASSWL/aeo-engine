#!/usr/bin/env python3
"""AEO Engine · J1 —— 已签发台账行 → 站点内容文件。

依据：Build Spec · Phase 3「J2 发布与语料守护」+ 2026-08-10 Shawn 拍板
      「AEO 内容复用 blog 管线」。

补的是签发与发布之间那段真空：台账行改成「已签发」之后，此前没有任何东西
把 outbox 里的草稿变成站点上的一页。2026-08-10 那 4 行就是这么静静躺着的——
签发那一刻它同时脱离了 sla_check 的草稿积压规则，从所有雷达上消失。

它做什么：
    读台账里「状态=已签发 且 发布链接为空」的行 → 按「面向」匹配 outbox 草稿
    → 生成 <posts_dir>/<slug>.md（blog 字段 + AEO 字段合并的 frontmatter）

它不做什么：
    * **不写 Notion。** 一个字都不写。台账状态推成「已发布」是站点 build 里
      aeo-ledger-writeback.mjs 的活，且只在页面真的构建成功之后发生。
      在这里抢先写，就等于用「文件生成了」冒充「页面上线了」。
    * **不碰 git。** 提交、开 PR、合并都是真人动作。

两个字段脚本不猜，缺了就拒绝：
    segment       —— 面向哪个 segment 是判断，不是查表
    anchor_terms  —— 必须落在 facts.json 已确认集合内，选哪几个是编辑决定

签发日期不再要真人填（Shawn 2026-08-12：「签发我只改签发状态，签发日期自动回填」）：
    scripts/ledger_signoff_date.py 每天 08:15 按 last_edited_time 回填。
    本脚本仍然会在日期为空时挡一下，但那是「等回填跑到」，不是「你去手填」。

用法：
    python3 scripts/j1_publish.py --list
    python3 scripts/j1_publish.py --ledger-id <id> --segment B \\
        --anchor-terms "AI video search,queryable video library"
    python3 scripts/j1_publish.py --ledger-id <id> --segment B \\
        --anchor-terms "..." --commit
退出码：0 正常；4 被拒绝；2 缺凭据；1 执行失败
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "j1_publish"
EXIT_REFUSED = 4

LEDGER_SIGNED = "已签发"
# 草稿文件名形如 j1_draft_2026-08-06_<slug>.md / j1_sample_2026-08-06_<slug>.md
DRAFT_RE = re.compile(r"^j1_(?:draft|sample)_\d{4}-\d{2}-\d{2}_(?P<slug>.+)\.md$")
# 证据编号从草稿头部注释里捞。样稿写成 `SIG-xxx(说明)`、产线稿写成 `SIG-xxx, SIG-yyy`，
# 两种格式都有，所以按编号本身的形状抓，不按分隔符切。
#
# ⚠️ 2026-08-13 修：原来只认 WL-/SIG-，于是**任何用降级证据写的页都发不出去**——
# 拒稿理由是「草稿头部注释里没抓到证据编号」，读起来像草稿坏了，其实是这条正则
# 比产线少认两种编号。PRB-/KW- 是 2026-08-12 放宽生产条件时加的降级证据源
# （j1_runner.py、j1_evidence.py、config/j1.yaml 当天就认了），站点侧
# aeo-lint.mjs 也在同一轮补上（vivu_web 60dccdd），唯独这里漏了。
# ai-video-online（证据全是 PRB-/KW-）是第一篇真的撞上的。
# 四种编号的强度口径见 j1_evidence.py 与 aeo-lint.mjs，三处必须一致。
EVIDENCE_RE = re.compile(
    r"\b(WL-\d{4}-\d{2}-\d{2}-[^\s,(（]+|(?:SIG|PRB|KW)-[0-9a-zA-Z]+)")
COMMENT_RE = re.compile(r"\A\s*<!--.*?-->\s*", re.S)
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def confirmed_anchor_terms(facts_path):
    """facts.json 里 status=已确认 的锚点词集合。lint 拿同一个集合校验。"""
    with open(facts_path, encoding="utf-8") as fh:
        facts = json.load(fh)
    terms = []
    for group in ("primary", "secondary"):
        for entry in (facts.get("anchor_terms", {}) or {}).get(group, []) or []:
            if entry.get("status") == "已确认" and entry.get("term"):
                terms.append(entry["term"])
    return terms


def find_drafts():
    """outbox 里的草稿：query 归一化键 → 文件信息。"""
    out = {}
    for name in sorted(os.listdir(ac.OUTBOX_DIR)):
        m = DRAFT_RE.match(name)
        if not m:
            continue
        path = os.path.join(ac.OUTBOX_DIR, name)
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        header = COMMENT_RE.match(raw)
        if not header:
            continue
        head_text = header.group(0)
        qm = re.search(r"query:\s*(.+)", head_text)
        if not qm:
            continue
        # 样稿的 query 行后面跟着「(Query 库,数据来源=...)」这类注解，切掉。
        query = re.split(r"[(（]", qm.group(1).strip())[0].strip()
        out[sc.norm_query(query)] = {
            "file": name, "path": path, "slug": m.group("slug"),
            "query": query, "raw": raw, "header": head_text,
        }
    return out


def split_body(raw):
    """剥掉头部 HTML 注释与 H1，返回 (title, body)。

    H1 必须剥：站点的 BlogPost.tsx 自己用 frontmatter 的 title 渲染 <h1>，
    正文里再留一个就是一页两个 H1。
    """
    body = COMMENT_RE.sub("", raw).lstrip()
    title = None
    m = re.match(r"#\s+(.+?)\s*\n", body)
    if m:
        title = m.group(1).strip()
        body = body[m.end():].lstrip()
    return title, body


def extract_description(body, max_chars):
    """取正文第一段，按句号边界截到上限内。

    这是机器摘的，不是编辑写的——调用方会在输出里明说这一点。
    """
    para = ""
    for block in body.split("\n\n"):
        text = " ".join(block.split())
        if text and not text.startswith(("#", "-", "*", ">", "|")):
            para = text
            break
    if len(para) <= max_chars:
        return para
    cut = para[:max_chars + 1]
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if stop > 40:
        return cut[:stop + 1].strip()
    return para[:max_chars].rsplit(" ", 1)[0].strip()


def yaml_scalar(value):
    """frontmatter 标量。两边的解析器都只吃朴素子集，含冒号就加引号。"""
    text = str(value)
    if re.search(r'[:#"\']|^\s|\s$', text):
        return '"{}"'.format(text.replace('"', "'"))
    return text


def render_frontmatter(fields, lists):
    lines = ["---"]
    for key, value in fields:
        if value is None or value == "":
            continue
        lines.append("{}: {}".format(key, yaml_scalar(value)))
    for key, values in lists:
        lines.append("{}:".format(key))
        for v in values:
            lines.append("  - {}".format(yaml_scalar(v)))
    lines.append("---")
    return "\n".join(lines)


def ledger_view(rows, drafts):
    """待发布清单：状态=已签发 且 发布链接为空。"""
    out = []
    for row in rows:
        p = row.get("properties", {})
        if ac.select_name(p, "状态") != LEDGER_SIGNED:
            continue
        if (p.get("发布链接") or {}).get("url"):
            continue
        facing = ac.rich_text(p, "面向")
        draft = drafts.get(sc.norm_query(facing))
        out.append({
            "ledger_id": row["id"],
            "面向": facing,
            "类型": ac.select_name(p, "类型"),
            "签发日期": ((p.get("签发日期") or {}).get("date") or {}).get("start"),
            "draft": draft["file"] if draft else None,
            "slug_default": draft["slug"] if draft else None,
        })
    return out


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true",
                        help="只列待发布的台账行，不生成任何文件")
    parser.add_argument("--ledger-id", dest="ledger_id", default=None,
                        help="要发布的台账行 ID")
    parser.add_argument("--segment", default=None, help="A/B/C/D/E")
    parser.add_argument("--anchor-terms", dest="anchor_terms", default=None,
                        help="逗号分隔，必须都在 facts.json 已确认集合里")
    parser.add_argument("--slug", default=None, help="覆盖默认 slug（默认取草稿文件名）")
    parser.add_argument("--description", default=None,
                        help="meta description。不给就从正文首段摘，并在输出里标注")
    parser.add_argument("--draft", default=None, help="指定草稿文件（默认按「面向」匹配）")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的内容文件")
    args = parser.parse_args()

    result = {"script": SCRIPT, "mode": sc.resolve_mode(args), "wrote_file": False}
    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        cfg = ac.load_config("j1.yaml")
        pub = cfg["publish"]
        segments = ac.load_config("segments.yaml")["segments"]

        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        rows = notion.query_all(env["DS_LEDGER"])
        drafts = find_drafts()
        pending = ledger_view(rows, drafts)
        result["pending"] = pending

        if args.list or not args.ledger_id:
            result["note"] = ("待发布 = 台账状态「已签发」且发布链接为空。"
                              "给 --ledger-id 才会生成文件。")
            sc.emit(SCRIPT, result, th)
            return 0

        key = args.ledger_id.replace("-", "")
        row = next((r for r in rows if r["id"].replace("-", "") == key), None)
        if row is None:
            result["refused"] = "台账里找不到这一行：{}".format(args.ledger_id)
            sc.emit(SCRIPT, result, th)
            return EXIT_REFUSED

        p = row.get("properties", {})
        status = ac.select_name(p, "状态")
        facing = ac.rich_text(p, "面向")
        signed_off = ((p.get("签发日期") or {}).get("date") or {}).get("start")
        published_url = (p.get("发布链接") or {}).get("url")
        result["ledger"] = {"id": row["id"], "状态": status, "面向": facing,
                            "签发日期": signed_off, "发布链接": published_url}

        refusals = []
        if status != LEDGER_SIGNED:
            refusals.append("台账状态是「{}」，不是「{}」。签发是真人动作，"
                            "脚本不代签。".format(status, LEDGER_SIGNED))
        if published_url:
            refusals.append("这一行已有发布链接 {}，不重复生成。".format(published_url))
        if not signed_off:
            # 2026-08-12 Shawn 拍板「签发我只改签发状态，签发日期自动回填」。
            # 原来这里直接拒稿，理由是站点 lint 两头堵死（填了 frontmatter 的
            # signed_off 报「台账签发日期是空的」，不填报「signed_off 为空」）。
            # 那个 lint 约束一个字没变——变的是**谁来填**：
            # scripts/ledger_signoff_date.py 每天 08:15 回填，取 last_edited_time。
            #
            # 这里因此从「拒稿」降为「等一轮」：不是不该发，是回填还没跑到。
            # 仍然不在本脚本里就地补写——本脚本的契约是**不写 Notion 一个字**
            # （状态推「已发布」是站点 build 里 aeo-ledger-writeback.mjs 的活），
            # 破这条会开出第二条台账写路径，两边迟早打架。
            refusals.append(
                "台账「签发日期」还是空的——signoff 回填任务（每天 08:15，"
                "scripts/ledger_signoff_date.py）还没跑到这一行。"
                "**你不用手填**：等下一轮回填，或立刻手动跑一次 "
                "`python3 scripts/ledger_signoff_date.py --commit` 再发。"
                "带空日期发出去会让站点 lint build fail，所以这里先挡住。")

        draft = None
        if args.draft:
            path = args.draft if os.path.isabs(args.draft) \
                else os.path.join(ac.OUTBOX_DIR, args.draft)
            if not os.path.exists(path):
                refusals.append("指定的草稿不存在：{}".format(path))
            else:
                with open(path, encoding="utf-8") as fh:
                    raw = fh.read()
                m = DRAFT_RE.match(os.path.basename(path))
                draft = {"file": os.path.basename(path), "path": path, "raw": raw,
                         "slug": m.group("slug") if m else None}
        else:
            draft = drafts.get(sc.norm_query(facing))
            if draft is None:
                refusals.append("outbox 里找不到「面向」对得上的草稿：{}。"
                                "用 --draft 指定。".format(facing))

        if args.segment not in segments:
            refusals.append("--segment 必须是 {} 之一。面向哪个 segment 是判断，"
                            "脚本不猜。".format("/".join(sorted(segments))))

        allowed_terms = confirmed_anchor_terms(cfg["facts_file"])
        terms = [t.strip() for t in (args.anchor_terms or "").split(",") if t.strip()]
        if not terms:
            refusals.append("--anchor-terms 不能为空。可选：{}".format(
                " / ".join(allowed_terms)))
        for t in terms:
            if t not in allowed_terms:
                refusals.append("锚点词「{}」不在 facts.json 已确认集合里。"
                                "要加新词，先去 facts.json 登记出处。可选：{}".format(
                                    t, " / ".join(allowed_terms)))

        if refusals:
            result["refused"] = refusals
            sc.emit(SCRIPT, result, th)
            return EXIT_REFUSED

        title, body = split_body(draft["raw"])
        evidence = []
        for code in EVIDENCE_RE.findall(COMMENT_RE.match(draft["raw"]).group(0)):
            if code not in evidence:
                evidence.append(code)

        slug = args.slug or draft["slug"]
        if not slug or not SLUG_RE.match(slug):
            result["refused"] = "slug「{}」不是小写连字符形式，用 --slug 给一个。".format(slug)
            sc.emit(SCRIPT, result, th)
            return EXIT_REFUSED
        if not title:
            result["refused"] = "草稿里没有 H1 标题，取不到 title：{}".format(draft["file"])
            sc.emit(SCRIPT, result, th)
            return EXIT_REFUSED
        if not evidence:
            result["refused"] = "草稿头部注释里没抓到证据编号：{}".format(draft["file"])
            sc.emit(SCRIPT, result, th)
            return EXIT_REFUSED

        description = args.description or extract_description(
            body, pub["description_max_chars"])
        ledger_type = ac.select_name(p, "类型")
        fm_type = "compare" if ledger_type == "对比页" else "aeo"

        content = render_frontmatter(
            [("title", title),
             ("description", description),
             ("slug", slug),
             ("date", signed_off),
             ("author", pub["author"]),
             ("type", fm_type),
             ("segment", args.segment),
             ("query", facing),
             ("signed_off", signed_off),
             ("ledger_id", row["id"])],
            [("evidence", evidence),
             ("anchor_terms", terms)],
        ) + "\n\n" + body.rstrip() + "\n"

        target = os.path.join(pub["posts_dir"], "{}.md".format(slug))
        result["target"] = target
        result["url_expected"] = "{}{}/{}".format(pub["site"], pub["url_prefix"], slug)
        result["title"] = title
        result["evidence"] = evidence
        result["anchor_terms"] = terms
        result["description_source"] = "真人给定" if args.description else "机器从正文首段摘的"
        if not args.description:
            result["description_warning"] = (
                "description 是机器摘的，它会原样出现在搜索结果里。PR 里请真人过一眼。")
        result["description"] = description

        if os.path.exists(target) and not args.force:
            result["refused"] = "目标文件已存在：{}（要覆盖加 --force）".format(target)
            sc.emit(SCRIPT, result, th)
            return EXIT_REFUSED

        if sc.resolve_mode(args) == "commit":
            os.makedirs(pub["posts_dir"], exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(content)
            result["wrote_file"] = True
        else:
            result["preview"] = content

        result["next_steps"] = [
            "在 vivu_web 跑 npm run aeo:lint 确认这一页过闸",
            "git 提交 + 开 PR + 合并进 main（真人动作，本脚本不碰 git）",
            "生产构建成功后 aeo-ledger-writeback.mjs 才把台账推成「已发布」并写回发布链接",
        ]
        sc.emit(SCRIPT, result, th)
        return 0

    except KeyError as err:
        print("缺配置或凭据：{}".format(err), file=sys.stderr)
        return 2
    except Exception as err:                                   # noqa: BLE001
        print("{} 执行失败：{}".format(SCRIPT, err), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
