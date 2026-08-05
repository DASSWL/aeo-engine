#!/usr/bin/env python3
"""AEO Engine · J4 —— 待触达队列 → 草稿 → 推 Telegram 群。

依据：Build Spec · Phase 3「J4 Outreach」与「详细实现规格补充 · J4 补充」。

三段式，理由同 Phase 1 的 run_friday_review.sh：LLM 不进 Python。
  1. plan     —— 纯 Python。读水箱、算窗口、定切入角与渠道、过证据闸门 → 草稿请求包
  2. (claude) —— 由 run_draft_runner.sh 调 claude -p，加载真 skill 产出草稿正文
  3. assemble —— 纯 Python。把草稿正文装进 spec 规定的 Telegram 模板 → outbox/

这么切的好处：窗口判定、闸门、去重、模板全部可离线复算与回归测试，
LLM 只负责它唯一擅长的那件事（写英文），它挂了不会让队列算错。

零发送红线：本脚本只写 outbox/*.md。发送由 sales agent 做，收件人永远是那个 Telegram 群，
永远不是 prospect 本人。config/outreach.yaml 的 zero_send 三个开关任一为 false 即拒绝运行。

用法：
    python3 scripts/draft_runner.py                          # dry-run，只算队列与闸门
    python3 scripts/draft_runner.py --emit-prompt outbox/p.md
    python3 scripts/draft_runner.py --assemble logs/claude_out.txt --commit
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402
import skill_check as sk     # noqa: E402

SCRIPT = "draft_runner"
COOLDOWN_STATE = "draft_runner_sent.json"

DRAFT_BEGIN = "===DRAFT {}==="
DRAFT_END = "===END {}==="
SKILL_MISSING_TPL = "[SKILL MISSING: {}]"


# --------------------------------------------------------------------------
# 零发送红线
# --------------------------------------------------------------------------

def assert_zero_send(cfg):
    z = cfg["zero_send"]
    off = [k for k, v in z.items() if v is not True]
    if off:
        raise RuntimeError(
            "零发送红线被关闭：{}。这不是可配置项——J4 的全部意义是备弹不发送，"
            "关掉它等于让脚本直接对外发信。拒绝运行。".format("、".join(off)))


# --------------------------------------------------------------------------
# 队列
# --------------------------------------------------------------------------

def row_window(props, q, sla, tz):
    """返回 (窗口基准时刻, 窗口小时数, 依据说明)。判不出基准即 (None, None, 原因)。

    口径与 scripts/sla_check.py 规则一/二逐条对齐——两个脚本对「同一行什么时候到期」
    必须给同一个答案，否则 sla 报了警而 J4 不出草稿，或者反过来。
      * 来源 = referral → 基准是「入箱日期」，窗口 referral_hours（sla 规则二）
      * 信号类型 = signal → 基准是「触达时限起算」，窗口 signal_hours（sla 规则一）
    referral 优先判：一条 referral 行的信号类型也可能是 signal，但它该走 48h。
    """
    if ac.select_name(props, "来源") == q["referral_source_value"]:
        base = ac.date_prop(props, "入箱日期", tz)
        if base is None:
            return None, None, "来源=referral 但「入箱日期」为空，算不出窗口"
        return base, sla["referral_hours"], "referral / 入箱日期 + {}h".format(
            sla["referral_hours"])

    if ac.select_name(props, "信号类型") == q["signal_type_value"]:
        base = ac.date_prop(props, "触达时限起算", tz)
        if base is None:
            return None, None, "信号类型=signal 但「触达时限起算」为空，算不出窗口"
        return base, sla["signal_hours"], "signal / 触达时限起算 + {}h".format(
            sla["signal_hours"])

    return None, None, "既非 referral 也非 signal，无窗口基准（spec 未定义此形态）"


def build_queue(pipeline, cfg, sla, tz, now, cooldown):
    """待触达队列。返回 (选中项, 逐条落选原因)。"""
    q = cfg["queue"]
    selected, skipped = [], []
    cd = timedelta(hours=q["redraft_cooldown_hours"])

    for row in pipeline:
        props = row.get("properties", {})
        rid = row.get("id")
        name = ac.title_text(props, "人名")
        status = ac.select_name(props, "状态")

        if status not in q["statuses"]:
            continue  # 不是待触达状态，连落选都不算，不噪音

        base, hours, why = row_window(props, q, sla, tz)
        if base is None:
            skipped.append({"id": rid, "人名": name, "reason": why})
            continue

        deadline = base + timedelta(hours=hours)
        remaining = (deadline - now).total_seconds() / 3600.0
        overdue = remaining <= 0

        if overdue and not q["include_overdue"]:
            skipped.append({"id": rid, "人名": name,
                            "reason": "已超时且 include_overdue=false"})
            continue
        if q["due_within_hours"] is not None and remaining > q["due_within_hours"]:
            skipped.append({"id": rid, "人名": name,
                            "reason": "剩余 {:.1f}h 超出 due_within_hours".format(remaining)})
            continue

        last = cooldown.get(rid)
        if last:
            last_dt = datetime.fromisoformat(last)
            if now - last_dt < cd:
                skipped.append({
                    "id": rid, "人名": name,
                    "reason": "冷却期内（上次推送 {}，冷却 {}h）".format(
                        last, q["redraft_cooldown_hours"])})
                continue

        selected.append({
            "id": rid,
            "url": row.get("url") or "",
            "人名": name,
            "公司": ac.rich_text(props, "公司"),
            "角色标签": ac.select_name(props, "角色标签"),
            "segment": ac.select_name(props, "segment"),
            "状态": status,
            "来源": ac.select_name(props, "来源"),
            "信号类型": ac.select_name(props, "信号类型"),
            "信号原文": ac.rich_text(props, "信号原文"),
            "来源链接": (props.get("来源链接") or {}).get("url") or "",
            "下一步动作": ac.rich_text(props, "下一步动作"),
            "引荐来源": ac.relation_ids(props, "引荐来源"),
            "window_basis": why,
            "deadline": deadline.isoformat(),
            "remaining_hours": round(remaining, 1),
            "overdue": overdue,
        })

    # 按剩余时限升序——spec §J4 补充「按剩余时限升序排列」。
    # Notion 视图表达不了这个排序（Phase 0 已记），所以真正的排序在这里做。
    selected.sort(key=lambda r: r["remaining_hours"])
    return selected, skipped


# --------------------------------------------------------------------------
# 切入角与渠道
# --------------------------------------------------------------------------

def hiring_markers(cfg, segments_cfg):
    a = cfg["angles"]["hiring_override"]
    markers = [m.lower() for m in a["markers"]]
    if a.get("also_use_segment_hiring_keywords"):
        for seg in (segments_cfg.get("segments") or {}).values():
            markers.extend(k.lower() for k in (seg.get("hiring_keywords") or []))
    return sorted(set(markers))


def pick_angle(item, cfg, markers):
    """招聘覆盖优先于角色。返回 (angle dict, 命中的招聘词或 None)。"""
    haystack = " ".join([item.get("信号原文") or "", item.get("下一步动作") or ""]).lower()
    hit = next((m for m in markers if m and m in haystack), None)
    if hit:
        return cfg["angles"]["hiring_override"], hit
    by_role = cfg["angles"]["by_role"]
    role = item.get("角色标签")
    if role in by_role:
        return by_role[role], None
    # 角色标签为空：spec 没定义这种行的切入角。不猜，交真人。
    return {"id": "unspecified", "label": "角色标签为空，切入角待真人指定",
            "brief": "本行「角色标签」为空，无法按 spec 的分角色规则选切入角。"
                     "草稿只写到可复制的骨架，个性化那一句留空。"}, None


def pick_channel(item, cfg):
    link = (item.get("来源链接") or "").lower()
    source = item.get("来源")
    for rule in cfg["channels"]["rules"]:
        if rule.get("when_source") and rule["when_source"] != source:
            continue
        if rule.get("when_link_contains") and rule["when_link_contains"] not in link:
            continue
        return rule
    return cfg["channels"]["fallback"]


def evidence_gate(item, channel, cfg):
    """返回 (verdict, quote_mode, 说明)。verdict ∈ {ok, refuse}。

    闸门必须真的拒绝——缺证据的行不产出草稿，产出的是一条「缺什么」。
    """
    g = cfg["evidence_gate"]
    cid = channel.get("id")

    for field in channel.get("requires_fields") or []:
        val = item.get(field)
        if not val:
            return "refuse", None, "渠道「{}」要求字段「{}」非空，当前为空".format(
                channel.get("label"), field)

    if cid in (g.get("require_signal_text_for") or []):
        text = (item.get("信号原文") or "").strip()
        if not text:
            return "refuse", None, (
                "渠道「{}」按 spec 必须引用信号原文，但本行「信号原文」为空。"
                "缺的就是这一条，补上即可生成。".format(channel.get("label")))
        if any(m in text for m in (g.get("not_real_quote_markers") or [])):
            if g.get("no_quote_downgrade"):
                return "ok", "no_quote", (
                    "「信号原文」标了非原文引用（Apollo 名单行），不是本人说的话。"
                    "降级为不引用变体：不装熟、不伪造原话。")
            return "refuse", None, "「信号原文」非真实原话，且未开降级"
    return "ok", "quote", ""


# --------------------------------------------------------------------------
# prompt 与装配
# --------------------------------------------------------------------------

def build_prompt(items, cfg, segments_cfg, env, skill_report):
    """给 claude -p 的 prompt。真 skill 由 .claude/skills/ 暂存后自动可见。"""
    ov = cfg["skill_overrides"]["vivu-outreach"]
    blocked = set(skill_report.get("blocked_steps") or [])
    missing = skill_report.get("missing_required") or []

    lines = [
        "# 任务：为 AEO Engine 的待触达队列逐条生成 warm 触达草稿",
        "",
        "你在给 Vivu 的创始人 Shawn 备弹。草稿由他本人过目后手动发出，你不发送任何东西。",
        "",
        "## 强制产线",
        "",
        "写任何一个字的英文之前，**先调用 ai-writing-guideline skill**"
        "（它会指向实时规则文件 ai_writings.md，每次读新的，不要用记忆里的版本）。",
        "这不是建议。这一步跳过，产出一律作废。",
        "",
    ]

    if missing:
        lines += [
            "## ⚠️ 缺失的 skill（如实标注，不许自己编一套顶替）",
            "",
            "以下 skill 在本机不存在：{}".format("、".join(missing)),
            "受影响的环节：{}。".format("、".join(sorted(blocked)) or "（无）"),
            "如果本次有任何一条草稿属于受影响环节，**不要凭自己的判断补写**——"
            "在该条草稿正文位置原样写 `{}`，并在草稿末尾一行说明缺什么。".format(
                SKILL_MISSING_TPL.format("skill 名")),
            "",
        ]

    lines += [
        "## 运行时覆盖（vivu-outreach skill 本体不改，以下覆盖只在本次生效）",
        "",
        "- **{}**：本次不写任何 Notion。草稿只推 Telegram 群，写库由 draft_runner 做。".format(
            "、".join(ov["disable_steps"]) + " 整步关闭"),
        "- **禁止写入的旧体系**：{}。这些是已封存的旧作战板，一个字都不许写进去。".format(
            "、".join(ov["forbidden_write_targets"])),
        "- 新五库的 database ID 为唯一合法写入目标（本次不写，仅声明口径）：",
    ]
    for key in ov["inject_db_keys"]:
        lines.append("  - {} = {}".format(key, env.get(key, "(未配置)")))
    if ov.get("forbid_apollo_calls"):
        lines += [
            "- **禁止调用任何 Apollo 工具**。富化在 Phase 2 已做过，草稿阶段再调是重复付费。",
        ]

    lines += [
        "",
        "## 输出格式（严格遵守，assemble 步骤按此解析）",
        "",
        "每条草稿包在这两行之间，中间只放可直接复制发送的正文，不要任何解释、"
        "不要前言、不要 markdown 代码围栏：",
        "",
        "```",
        DRAFT_BEGIN.format("<水箱行 ID>"),
        "<草稿正文>",
        DRAFT_END.format("<水箱行 ID>"),
        "```",
        "",
        "正文要求：",
        "- 直接可发送。不写「Hi [Name]」这种占位符，名字就写出来。",
        "- 长度按渠道来：LinkedIn 私信控制在 600 字符以内，否则对方不会读完。",
        "- 个性化只能用下面给出的事实。**给的事实之外一个字都不许编**——"
        "不许编定价、不许编集成、不许编客户结果、不许编他没说过的话。",
        "- quote_mode = quote 的条目：必须引用「信号原文」里他自己的话。",
        "- quote_mode = no_quote 的条目：**不许装熟**。这行的信号原文是名单命中条件，"
        "不是他说的话。按公司与岗位的公开事实写，不要假装读过他的帖子。",
        "",
        "## 本次条目",
        "",
    ]

    segs = segments_cfg.get("segments") or {}
    for it in items:
        seg = segs.get(it.get("segment") or "", {})
        lines += [
            "---",
            "",
            "### 水箱行 ID: {}".format(it["id"]),
            "",
            "- 收件人：{}".format(it["人名"] or "(无名)"),
            "- 公司：{}".format(it["公司"] or "(无)"),
            "- 角色标签：{}".format(it["角色标签"] or "(空)"),
            "- segment：{}（{}）".format(it.get("segment") or "-", seg.get("name") or "-"),
            "- segment 定义：{}".format(seg.get("definition") or "(无)"),
            "- 渠道：{}".format(it["channel_label"]),
            "- 切入角：{}".format(it["angle_label"]),
            "- 切入角要求：{}".format(it["angle_brief"]),
            "- quote_mode：{}".format(it["quote_mode"]),
            "- 信号原文：{}".format(it["信号原文"] or "(空)"),
            "- 来源链接：{}".format(it["来源链接"] or "(无)"),
            "- 到期：剩余 {}h{}".format(
                it["remaining_hours"], "（已逾期）" if it["overdue"] else ""),
            "",
        ]
        if it.get("hiring_hit"):
            lines += [
                "- ⚠️ 招聘信号命中（命中词：{}）。切入角已按 spec 换成"
                "「已选雇人翻素材这个替代方案」。不要写成劝他别招人。".format(it["hiring_hit"]),
                "",
            ]

    lines += [
        "---",
        "",
        "现在逐条输出。只输出上面规定的 DRAFT 包，包与包之间不要任何其他文字。",
    ]
    return "\n".join(lines)


def parse_drafts(text):
    """从 claude 输出里抽出 {row_id: 正文}。解析不到的行由调用方报缺。"""
    out = {}
    pattern = re.compile(
        r"===DRAFT\s+([0-9a-fA-F-]{32,36})===\s*\n(.*?)\n?===END\s+\1===",
        re.DOTALL)
    for m in pattern.finditer(text):
        out[m.group(1)] = m.group(2).strip()
    return out


def render_message(item, body, cfg):
    """spec §J4 补充的 Telegram 模板，字段顺序逐条照抄。"""
    countdown = ("已逾期 {}h".format(abs(item["remaining_hours"]))
                 if item["overdue"] else "剩余 {}h".format(item["remaining_hours"]))
    lines = [
        "📮 AEO · J4 触达草稿",
        "",
        "收件人：{}".format(item["人名"] or "(无名)"),
        "公司：{}".format(item["公司"] or "(无)"),
        "角色：{}".format(item["角色标签"] or "(空)"),
        "渠道：{}".format(item["channel_label"]),
        "到期倒计时：{}".format(countdown),
        "切入角：{}".format(item["angle_label"]),
        "",
        "—— 草稿正文（可直接复制）——",
        "",
        body,
        "",
        "—— 以上 ——",
        "",
        "水箱行 ID：{}".format(item["id"]),
        "水箱行：{}".format(item["url"]),
        "",
        "发出后请回复：sent {}".format(item["id"]),
    ]
    text = "\n".join(lines)
    cap = cfg["telegram"]["max_chars_per_message"]
    if len(text) > cap:
        text = text[:cap - 40] + "\n\n…（超长已截断，请看 outbox 原文）"
    return text


# --------------------------------------------------------------------------
# 冷却状态
# --------------------------------------------------------------------------

def load_cooldown():
    path = os.path.join(ac.DATA_DIR, COOLDOWN_STATE)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_cooldown(state):
    path = os.path.join(ac.DATA_DIR, COOLDOWN_STATE)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    return path


# --------------------------------------------------------------------------

def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--emit-prompt", dest="emit_prompt", default=None,
                        help="把 claude prompt 写到这个路径后退出")
    parser.add_argument("--assemble", default=None,
                        help="读 claude 的输出文件，装配成 Telegram 消息")
    parser.add_argument("--plan-file", dest="plan_file", default=None,
                        help="assemble 时读取的 plan JSON，默认取当日最新")
    parser.add_argument("--limit", type=int, default=None, help="临时压低本次条数")
    args = parser.parse_args()

    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        cfg = ac.load_config("outreach.yaml")
        segments_cfg = ac.load_config("segments.yaml")
        assert_zero_send(cfg)

        from zoneinfo import ZoneInfo
        tz = ZoneInfo(th["week_window"]["timezone"])
        now = datetime.now(tz)

        # skill 可用性：缺什么如实带进 plan 与 prompt
        skill_report = {"missing_required": [], "blocked_steps": [], "skills": {}}
        registry = cfg["skills"]["registry"]
        for name, entry in registry.items():
            path, tried = sk.resolve_skill(name, entry)
            skill_report["skills"][name] = {"available": bool(path), "resolved_path": path}
            if not path and entry.get("required"):
                skill_report["missing_required"].append(name)
                skill_report["blocked_steps"].extend(entry.get("used_by") or [])
        skill_report["blocked_steps"] = sorted(set(skill_report["blocked_steps"]))

        # ---- assemble 分支 ----
        if args.assemble:
            plan_path = args.plan_file or os.path.join(
                ac.LOGS_DIR, "{}_plan_{}.json".format(SCRIPT, now.strftime("%Y-%m-%d")))
            if not os.path.exists(plan_path):
                raise RuntimeError("找不到 plan 文件：{}（先跑一次 plan）".format(plan_path))
            with open(plan_path, encoding="utf-8") as fh:
                plan = json.load(fh)
            with open(args.assemble, encoding="utf-8") as fh:
                bodies = parse_drafts(fh.read())

            written, unparsed = [], []
            state = load_cooldown()
            seen_files = {}
            for it in plan["drafts"]:
                body = bodies.get(it["id"])
                if not body:
                    unparsed.append({"id": it["id"], "人名": it["人名"],
                                     "reason": "claude 输出里没有这一条的 DRAFT 包"})
                    continue
                msg = render_message(it, body, cfg)
                # 文件名必须用**完整** id，不能截断。
                # 2026-08-05 首次自动运行踩到：原实现取 id[:8]，而同一批写入的
                # Notion page ID 前 8 位完全相同（ID 按时间有序，那天 50 行全是
                # 3b3059d9 开头）。10 条草稿写成同一个文件互相覆盖，只剩最后一条，
                # 群里当天只收到 1 条，另外 9 条静默消失——脚本还报「装配 10 条」。
                fname = "j4_draft_{}_{}.md".format(
                    now.strftime("%Y-%m-%d"), it["id"].replace("-", ""))
                if fname in seen_files:
                    # 防线：文件名撞车宁可整体失败，也不要再静默覆盖一次。
                    raise RuntimeError(
                        "草稿文件名冲突：{} 同时来自水箱行 {} 与 {}。"
                        "覆盖会让其中一条草稿静默消失。".format(
                            fname, seen_files[fname], it["id"]))
                seen_files[fname] = it["id"]
                if sc.resolve_mode(args) == "commit":
                    ac.write_outbox(fname, msg)
                    state[it["id"]] = now.isoformat()
                written.append({"id": it["id"], "人名": it["人名"], "file": fname,
                                "chars": len(msg)})
            if sc.resolve_mode(args) == "commit":
                save_cooldown(state)

            result = {
                "script": SCRIPT, "step": "assemble", "mode": sc.resolve_mode(args),
                "generated_at": now.isoformat(),
                "messages": written, "unparsed": unparsed,
                "wrote_outbox": sc.resolve_mode(args) == "commit",
            }
            sc.emit(SCRIPT + "_assemble", result, th)
            return 0

        # ---- plan 分支 ----
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        pipeline = notion.query_all(env["DS_PIPELINE"])
        cooldown = load_cooldown()
        selected, skipped = build_queue(pipeline, cfg, th["sla"], tz, now, cooldown)

        markers = hiring_markers(cfg, segments_cfg)
        drafts, refused = [], []
        for it in selected:
            angle, hit = pick_angle(it, cfg, markers)
            channel = pick_channel(it, cfg)
            verdict, quote_mode, note = evidence_gate(it, channel, cfg)
            it.update({
                "angle_id": angle["id"], "angle_label": angle["label"],
                "angle_brief": angle["brief"], "hiring_hit": hit,
                "channel_id": channel.get("id"), "channel_label": channel.get("label"),
                "gate_note": note,
            })
            if verdict == "refuse":
                refused.append({"id": it["id"], "人名": it["人名"], "公司": it["公司"],
                                "channel": channel.get("label"), "missing": note})
                continue
            it["quote_mode"] = quote_mode
            drafts.append(it)

        cap = args.limit or cfg["queue"]["max_drafts_per_run"]
        overflow = drafts[cap:]
        drafts = drafts[:cap]

        result = {
            "script": SCRIPT, "step": "plan", "mode": sc.resolve_mode(args),
            "generated_at": now.isoformat(),
            "row_counts_read": {"pipeline": len(pipeline)},
            "queue": {
                "statuses": cfg["queue"]["statuses"],
                "candidates_in_window": len(selected),
                "drafts_planned": len(drafts),
                "refused_by_evidence_gate": len(refused),
                "deferred_over_cap": len(overflow),
                "cap": cap,
            },
            "skills": skill_report,
            "drafts": drafts,
            "refused": refused,
            "skipped": skipped,
            "deferred": [{"id": o["id"], "人名": o["人名"]} for o in overflow],
        }
        path = sc.emit(SCRIPT + "_plan", result, th)

        # emit 落的是 <script>_<date>.json，assemble 按固定名找，这里对齐一次
        plan_path = os.path.join(
            ac.LOGS_DIR, "{}_plan_{}.json".format(SCRIPT, now.strftime("%Y-%m-%d")))
        if os.path.abspath(path) != os.path.abspath(plan_path):
            with open(plan_path, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=2)

        if args.emit_prompt:
            prompt = build_prompt(drafts, cfg, segments_cfg, env, skill_report)
            with open(args.emit_prompt, "w", encoding="utf-8") as fh:
                fh.write(prompt)
            print("PROMPT: {}".format(args.emit_prompt), file=sys.stderr)
            print("DRAFTS_PLANNED: {}".format(len(drafts)), file=sys.stderr)
        return 0

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
