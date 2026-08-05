#!/usr/bin/env python3
"""AEO Engine · J4 —— 建 Apollo 冷 outreach sequence（建成即暂停）。

依据：Build Spec · Phase 3「J4 冷 outreach 闭环：Apollo sequence 按 segment 建，
      内容用 vivu-outreach 整条产线产出」、§J4 补充「命名规范 seq_段字母_v版本」。

本次范围：只建 A 段第一版的第一封（seq_A_v1，先只建 A 段）。

**sequence 的启动键永远在 Apollo 界面由真人按。**
本脚本以 active=false 建，且不提供任何激活参数。要激活只能去 Apollo 界面点。
理由不是谨慎，是算术：sequence 一旦 active，加进去的人下一个发送窗口就会真的收到邮件。

按量计费防线（spec 硬约束）：
  * 默认 dry-run，--commit 才动 Apollo
  * dry-run 必打印费用预估与当前 credit 余额
  * 防重复付费预筛：建之前先按名字搜一遍，同名已存在即拒绝，不重复建
  * --no-sequence 关闭开关：整个建 sequence 动作跳过
  * 本脚本**不加联系人**（add_contacts 配置为 false）。加人才是花钱的那一步。

用法：
    python3 scripts/apollo_sequence.py                          # dry-run + 预估
    python3 scripts/apollo_sequence.py --emit-prompt logs/p.md  # 产出正文的 prompt
    python3 scripts/apollo_sequence.py --body logs/body.txt --commit
    python3 scripts/apollo_sequence.py --no-sequence            # 关闭开关
"""

import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402
import skill_check as sk     # noqa: E402

SCRIPT = "apollo_sequence"
EXIT_REJECTED = 4

SUBJECT_MARK = "SUBJECT:"
BODY_MARK = "BODY:"


def apollo(session, cfg_scan, api_key, method, path, payload=None):
    url = cfg_scan["apollo"]["base_url"] + path
    headers = {cfg_scan["apollo"]["auth_header"]: api_key,
               "Content-Type": "application/json", "accept": "application/json"}
    resp = session.request(method, url, json=payload, headers=headers, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError("Apollo {} {} → HTTP {}：{}".format(
            method, path, resp.status_code, resp.text[:400]))
    return resp.json()


def credit_balance(session, cfg_scan, api_key):
    """当前 credit 余额。取不到就如实返回 None，不猜。

    字段名 `num_credits_remaining` 是 2026-08-04 实测确认的（读到 1315，与 Phase 2
    §五 记的期末余额 1315 逐位一致）。不要改成看起来更合理的 `credits_remaining`——
    那个键不存在，读出来会是 None，于是费用预估里的余额永远是空的，
    而空余额看起来像「查过了没事」，比报错更危险。
    """
    try:
        body = apollo(session, cfg_scan, api_key, "GET",
                      "/users/api_profile?include_credit_usage=true")
        if isinstance(body, dict):
            return body.get("num_credits_remaining")
        return None
    except Exception:  # noqa: BLE001
        return None


def parse_body_file(path):
    """claude 产出的正文文件 → (subject, body_html)。

    格式约定写在 prompt 里，解析失败就报错而不是猜——猜错会把一整封错内容建进 Apollo。
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^{}\s*(.+?)\s*$".format(re.escape(SUBJECT_MARK)), text, re.M)
    if not m:
        raise RuntimeError("正文文件里找不到 {} 行：{}".format(SUBJECT_MARK, path))
    subject = m.group(1).strip()
    i = text.find(BODY_MARK)
    if i < 0:
        raise RuntimeError("正文文件里找不到 {} 段：{}".format(BODY_MARK, path))
    body = text[i + len(BODY_MARK):].strip()
    if not body:
        raise RuntimeError("{} 段为空：{}".format(BODY_MARK, path))
    return subject, body


def build_prompt(cfg, segments_cfg, skill_report):
    seq = cfg["sequence"]["build_now"]
    seg_key = seq["segment"]
    seg = (segments_cfg.get("segments") or {}).get(seg_key, {})
    missing = skill_report.get("missing_required") or []

    lines = [
        "# 任务：为 Apollo 冷 outreach sequence `{}` 写第一封（A 段第一封）".format(seq["name"]),
        "",
        "这封信会进 Apollo sequence，但**建成即暂停，不会自动发出**。真人在 Apollo 界面",
        "逐字审过并按下启动键之后，它才会发给 A 段的 200–400 家公司里的联系人。",
        "换句话说：这是一封要发给几百个陌生人的信，且只写一次。",
        "",
        "## 强制产线",
        "",
        "1. **先调用 vivu-outreach skill**，按它 Step 5「三封式 email cadence」里 Mail 1 的",
        "   规格写（真个性化首触、CTA 约一个写死时长的现场 demo）。",
        "2. **再调用 ai-writing-guideline skill**，它会指向实时规则文件，逐条自查后重写。",
        "",
        "## 运行时覆盖（skill 本体不改）",
        "",
        "- vivu-outreach 的 Step 1/2（公司调研、联系人调研）**本次不做**：sequence 是",
        "  一对多模板，没有具体的某一个人可以调研。个性化只能靠 Apollo 合并变量。",
        "- Step 6 Notion 交付整步关闭。不写任何 Notion。",
        "- 禁止调用任何 Apollo 工具（会花 credit）。",
        "",
    ]
    if missing:
        lines += [
            "- ⚠️ 以下 skill 本机不存在：{}。受影响的环节不许自己编一套顶替，".format(
                "、".join(missing)),
            "  在正文里原样标注缺什么。",
            "",
        ]

    lines += [
        "## 收件人画像（A 段，来自 config/segments.yaml，不要超出这里写的事实）",
        "",
        "- segment 名：{}".format(seg.get("name") or "-"),
        "- 定义：{}".format(seg.get("definition") or "-"),
        "- 目标 title（pain feeler）：{}".format(
            "、".join((seg.get("titles") or {}).get("pain_feeler") or [])),
        "- 目标 title（decision maker）：{}".format(
            "、".join((seg.get("titles") or {}).get("decision_maker") or [])),
        "- 公司规模：{} 人".format((seg.get("apollo") or {}).get("employee_count") or "-"),
        "",
        "## 事实边界（硬约束，违反即作废）",
        "",
        "- **Vivu 没有公开定价**。不许写任何价格、不许写「起步价」、不许暗示价格区间。",
        "- **Vivu 没有可引用的客户结果**。不许写「某客户节省了 X%」这类句子，一个都不许。",
        "- **不许编集成清单**。站点现有口径只有：通过 MCP 与 workflow 集成接入，有 API，",
        "  但设置过程不需要开发者。超出这句的具体集成名一个都不许写。",
        "- 不许编 benchmark 数字（准确率、召回率、索引速度）。",
        "- 可以写的：产品在做什么（在已有视频素材里按内容找到具体片段）、",
        "  这个岗位每周花在翻素材上的时间是一笔真实成本（作为提问，不作为断言）。",
        "",
        "## 合并变量",
        "",
        "可用：{{{{first_name}}}}、{{{{company}}}}、{{{{title}}}}。",
        "每个变量都要能在取值为空时读得通——Apollo 的合并变量经常是空的。",
        "",
        "## 输出格式（严格，脚本按此解析）",
        "",
        "只输出这两段，不要任何解释、不要 markdown 围栏：",
        "",
        "```",
        "{} <邮件主题，9 个词以内>".format(SUBJECT_MARK),
        "{}".format(BODY_MARK),
        "<邮件正文，HTML。25–85 词，50 词以内更好。单一 CTA。>",
        "```",
    ]
    return "\n".join(lines)


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--emit-prompt", dest="emit_prompt", default=None)
    parser.add_argument("--body", default=None, help="claude 产出的正文文件")
    parser.add_argument("--no-sequence", dest="no_sequence", action="store_true",
                        help="关闭开关：整个建 sequence 动作跳过，不调 Apollo")
    args = parser.parse_args()

    try:
        env = ac.load_env()
        th = ac.load_config("thresholds.yaml")
        cfg = ac.load_config("outreach.yaml")
        cfg_scan = ac.load_config("scan.yaml")
        segments_cfg = ac.load_config("segments.yaml")

        seq_cfg = cfg["sequence"]
        seq = seq_cfg["build_now"]
        mode = sc.resolve_mode(args)

        # 零发送红线：这一条不通过就不用往下走了
        if seq_cfg.get("create_as_active"):
            raise RuntimeError(
                "sequence.create_as_active = true。建成即激活等于建成即发送，"
                "spec 硬约束「sequence 暂停建」。拒绝运行。")

        if args.no_sequence:
            sc.emit(SCRIPT, {"script": SCRIPT, "status": "disabled_by_flag",
                             "flag": seq_cfg["billing"]["disable_flag"],
                             "wrote_apollo": False}, th)
            return 0

        skill_report = {"missing_required": [], "skills": {}}
        for name, entry in cfg["skills"]["registry"].items():
            path, _ = sk.resolve_skill(name, entry)
            skill_report["skills"][name] = {"available": bool(path)}
            if not path and entry.get("required"):
                skill_report["missing_required"].append(name)

        if args.emit_prompt:
            with open(args.emit_prompt, "w", encoding="utf-8") as fh:
                fh.write(build_prompt(cfg, segments_cfg, skill_report))
            print("PROMPT: {}".format(args.emit_prompt), file=sys.stderr)
            return 0

        api_key = env.get("APOLLO_API_KEY")
        if not api_key:
            sc.missing_credential(SCRIPT, "APOLLO_API_KEY",
                                  "建 sequence 需要 Apollo API key", th)

        session = requests.Session()

        # ---- 防重复付费预筛：同名 sequence 已存在即拒绝 ----
        # Phase 2 §五⑤ 的教训：计费动作不设防会每周重复烧钱。建 sequence 本身不花 credit，
        # 但重复建会造出两条同名 sequence，真人分不清该激活哪条——那个代价更贵。
        existing = apollo(session, cfg_scan, api_key, "POST",
                          "/emailer_campaigns/search",
                          {"q_name": seq["name"], "page": 1, "per_page": 25})
        dupes = [c for c in (existing.get("emailer_campaigns") or [])
                 if (c.get("name") or "").strip() == seq["name"]]

        balance = credit_balance(session, cfg_scan, api_key)

        # ---- 费用预估 ----
        billing = seq_cfg["billing"]
        estimate = {
            "create_sequence_credits": 0,
            "why_zero": "Apollo 的 credit 计在富化与邮箱/电话解锁上，建 sequence 壳不计费。",
            "add_contacts": billing["add_contacts"],
            "contacts_planned": 0,
            "would_cost_if_contacts_added": (
                "本次不加人。将来加人时，每个联系人若需解锁邮箱按 1 credit 计；"
                "A 段目标 {}–{} 家公司。".format(
                    seq_cfg["companies_per_segment_min"],
                    seq_cfg["companies_per_segment_max"])),
            "credit_balance_before": balance,
        }

        result = {
            "script": SCRIPT, "mode": mode,
            "sequence_name": seq["name"],
            "segment": seq["segment"], "version": seq["version"],
            "create_as_active": seq_cfg["create_as_active"],
            "steps_planned": seq_cfg["steps"],
            "duplicate_precheck": {
                "searched_name": seq["name"],
                "exact_matches": len(dupes),
                "matched_ids": [c.get("id") for c in dupes],
            },
            "cost_estimate": estimate,
            "skills": skill_report,
            "wrote_apollo": False,
        }

        if dupes:
            result["status"] = "rejected_duplicate"
            result["reason"] = (
                "已存在同名 sequence「{}」（{} 条）。不重复建。"
                "要改内容请在 Apollo 界面改，或先删掉旧的。".format(
                    seq["name"], len(dupes)))
            sc.emit(SCRIPT, result, th)
            sys.exit(EXIT_REJECTED)

        if mode != "commit":
            result["status"] = "dry_run"
            result["note"] = "未调用任何写接口。要真建请加 --commit 并给 --body。"
            sc.emit(SCRIPT, result, th)
            return 0

        # ---- 真建 ----
        if not args.body:
            raise RuntimeError("--commit 必须同时给 --body <正文文件>。"
                               "不给正文就建，等于建一条空壳 sequence。")
        subject, body_html = parse_body_file(args.body)
        result["content"] = {"subject": subject, "body_html": body_html,
                             "body_words": len(re.sub(r"<[^>]+>", " ", body_html).split())}

        # Apollo REST 建带内容的 sequence 要三步，不是一步。
        #
        # 2026-08-05 实测踩到：POST /emailer_campaigns 带 emailer_steps 会返回 200，
        # 但**静默忽略** steps——回读 num_steps = 0，建出来的是个空壳。
        # 只看 HTTP 200 就报「已建成」是错的，必须回读 num_steps 才算数。
        #   ① POST /emailer_campaigns          建壳（active=false）
        #   ② POST /emailer_steps              建步；Apollo 会顺带自动建 touch 与 template
        #   ③ PUT  /emailer_templates/<id>     把 subject 与 body_html 填进去
        step = seq_cfg["steps"][0]
        created = apollo(session, cfg_scan, api_key, "POST", "/emailer_campaigns", {
            "name": seq["name"],
            "permissions": "team_can_use",
            # ⚠️ 这一行是「暂停建」的实现。改成 true 即为建成就能发。
            "active": False,
        })
        camp = created.get("emailer_campaign") or created
        camp_id = camp.get("id")

        step_resp = apollo(session, cfg_scan, api_key, "POST", "/emailer_steps", {
            "emailer_campaign_id": camp_id,
            "position": 1,
            "type": step["type"],
            "wait_time": step["day"],
            "wait_mode": "day",
        })
        tmpl_id = ((step_resp.get("emailer_touch") or {}).get("emailer_template_id"))
        if not tmpl_id:
            raise RuntimeError(
                "建步成功但没拿到 emailer_template_id，正文无处可填。"
                "此时 sequence 是个有步骤没内容的壳，请去 Apollo 界面删掉重来。"
                "响应：{}".format(json.dumps(step_resp, ensure_ascii=False)[:400]))

        apollo(session, cfg_scan, api_key, "PUT",
               "/emailer_templates/{}".format(tmpl_id),
               {"subject": subject, "body_html": body_html})

        result["wrote_apollo"] = True
        result["status"] = "created"
        result["created"] = {"id": camp_id, "name": camp.get("name"),
                             "active": camp.get("active"),
                             "emailer_step_id": (step_resp.get("emailer_step") or {}).get("id"),
                             "emailer_template_id": tmpl_id}

        # 回读核对：不看自己的回执，重新拉线上状态确认它真的是暂停的
        back = apollo(session, cfg_scan, api_key, "POST", "/emailer_campaigns/search",
                      {"q_name": seq["name"], "page": 1, "per_page": 25})
        found = [c for c in (back.get("emailer_campaigns") or [])
                 if c.get("id") == camp.get("id")]
        result["readback"] = found[0] if found else None
        result["readback_active"] = (found[0].get("active") if found else None)
        result["readback_num_steps"] = (found[0].get("num_steps") if found else None)
        result["paused_proof"] = (
            "线上回读 active={}，非 true 即为暂停态。".format(result["readback_active"]))
        # 空壳自检：步骤没建成就别报「已建成」。见上方三步流程的注释。
        if not result["readback_num_steps"]:
            result["status"] = "created_but_empty"
            result["warning"] = (
                "回读 num_steps={}，步骤没落地，这是个空壳 sequence。"
                "别当它建好了。".format(result["readback_num_steps"]))
        result["credit_balance_after"] = credit_balance(session, cfg_scan, api_key)

        sc.emit(SCRIPT, result, th)
        return 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
