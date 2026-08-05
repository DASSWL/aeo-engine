#!/usr/bin/env python3
"""AEO Engine · A1 —— 按 segment 拉 Apollo 名单，同公司双角色配对入水箱。

依据：Build Spec · Phase 2 §一.2、§一.2「公司信号转名字链条」、§四「脚本要点 · apollo_poll.py」。

做四件事：
  1. 读 config/segments.yaml 的 apollo 搜索条件与 titles 映射 → Apollo people search
  2. 按公司分组，pain feeler 与 decision maker 配对；单边的降级入箱并标注缺失角色
  3. 读 data/apollo_backfill.csv（LinkedIn 周批扫写入的在招公司）做定向反查
  4. 写水箱（仅 --commit）

不做的事（spec 明文划走）：
  * 邮件回流轮询 —— sequence 在 J4 才建，回流轮询随 J4 启动。本脚本一行都不碰。

两个继承自 Phase 0 的口径，写库时必须遵守：
  * 「配对」是**单向** relation（Phase 0 差异①）。A 指向 B 不会让 B 自动指回 A，
    所以配对必须两边各写一次。不能假设反向可达。
  * 「信号原文」spec 标必填但 Notion 无原生约束（Phase 0 差异②）。Apollo 名单本身
    没有帖子原文，这里写的是名单来源与命中条件，并在文中显式标明「非原文引用」——
    不假装它是一句买家原话。

用法：
    python3 scripts/apollo_poll.py                        # dry-run，全 segment
    python3 scripts/apollo_poll.py --segments A --limit-companies 5
    python3 scripts/apollo_poll.py --backfill             # 只跑招聘公司反查
    python3 scripts/apollo_poll.py --commit
"""

import csv
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "apollo_poll"

ROLE_PAIN = "pain feeler"
ROLE_DM = "decision maker"
ROLE_BOTH = "双角色"


# --------------------------------------------------------------------------
# 请求构造
# --------------------------------------------------------------------------

def employee_ranges(spec_value):
    """segments.yaml 写的是 "51-1000"，Apollo 要的是 "51,1000"。"""
    if not spec_value:
        return []
    return [str(spec_value).replace("-", ",")]


def build_payload(apollo_cfg, titles, per_page, page=1, org_name=None):
    """一次 people search 的完整请求体。

    ⚠️ industries 的降级：Apollo 的行业筛选要 organization_industry_tag_ids（内部 ID），
       segments.yaml 里写的是行业名文本。这里把行业名并进 q_organization_keyword_tags
       当关键词用——**这是降级，不等价于行业筛选**，命中面会更宽也更糊。
       首轮实测时要专门看这一条（segments.yaml 疑点②）。
    """
    seg_apollo = apollo_cfg["segment_conditions"]
    tags = list(seg_apollo.get("keywords") or []) + list(seg_apollo.get("industries") or [])
    payload = {
        "person_titles": list(titles),
        "page": page,
        "per_page": per_page,
    }
    if org_name:
        payload["q_organization_name"] = org_name
    else:
        if tags:
            payload["q_organization_keyword_tags"] = tags
        ranges = employee_ranges(seg_apollo.get("employee_count"))
        if ranges:
            payload["organization_num_employees_ranges"] = ranges
    return payload


def call_apollo(session, base_url, path, header_name, api_key, payload):
    resp = session.post(
        base_url + path,
        json=payload,
        headers={header_name: api_key, "Content-Type": "application/json",
                 "Cache-Control": "no-cache", "accept": "application/json"},
        timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError("Apollo HTTP {}：{}".format(resp.status_code, resp.text[:300]))
    return resp.json()


# --------------------------------------------------------------------------
# 分组与配对
# --------------------------------------------------------------------------

def person_record(raw, segment, role):
    org = raw.get("organization") or {}
    return {
        "人名": " ".join(x for x in [raw.get("first_name"), raw.get("last_name")] if x).strip()
                or raw.get("name") or "",
        "公司": org.get("name") or "",
        "title": raw.get("title") or "",
        "来源链接": raw.get("linkedin_url") or "",
        "segment": segment,
        "role": role,
        "org_id": org.get("id") or (org.get("name") or "").strip().lower(),
        "apollo_person_id": raw.get("id"),
    }


def group_and_pair(people, segment, caps, single_side_tpl, max_companies=None):
    """按公司分组 → 双角色配对。返回待入箱的行（已按上限截断）。

    配对规则（spec §一.2「同公司配对」）：
      * 同一公司里既有 pain feeler 又有 decision maker → 各取一人配成一对
      * 同一个人的 title 同时命中两类 → 单人打「双角色」，不与他人配对
      * 只挖到单边 → 降级入箱，在「下一步动作」标注缺失的那一侧
    优先级：完整配对 > 双角色 > 单边。上限用满即停，不为凑数拿单边顶掉配对。

    ⚠️ 公司数上限 max_companies 必须在**分组之后**才截断。
       2026-08-04 首次实测踩到的坑：原实现把两次角色搜索的结果拼成一个列表，
       按出现顺序取前 N 家公司——pain feeler 的结果排在前面，于是选中的 N 家
       全来自 pain feeler 那一批，decision maker 只要不在这 N 家里就被丢掉，
       配对在截断阶段就已经不可能发生，`paired` 恒为 0。
       那个 0 是实现造出来的假象，不是 Apollo 或 segments.yaml 的证据。
    """
    by_org = {}
    for p in people:
        by_org.setdefault(p["org_id"], {"公司": p["公司"], "people": []})["people"].append(p)

    paired, dual, single = [], [], []
    for org in by_org.values():
        roles = {}
        for p in org["people"]:
            roles.setdefault(p["role"], []).append(p)
        both_ids = {p["apollo_person_id"] for p in roles.get(ROLE_PAIN, [])} & \
                   {p["apollo_person_id"] for p in roles.get(ROLE_DM, [])}
        if both_ids:
            for p in org["people"]:
                if p["apollo_person_id"] in both_ids:
                    dual.append([dict(p, 角色标签=ROLE_BOTH, 下一步动作="")])
                    break
            continue
        pf = roles.get(ROLE_PAIN) or []
        dm = roles.get(ROLE_DM) or []
        if pf and dm:
            paired.append([dict(pf[0], 角色标签=ROLE_PAIN, 下一步动作=""),
                           dict(dm[0], 角色标签=ROLE_DM, 下一步动作="")])
        elif pf:
            single.append([dict(pf[0], 角色标签=ROLE_PAIN,
                                下一步动作=single_side_tpl.format(missing_role=ROLE_DM))])
        elif dm:
            single.append([dict(dm[0], 角色标签=ROLE_DM,
                                下一步动作=single_side_tpl.format(missing_role=ROLE_PAIN))])

    # 先按优先级排队，再同时受「行数上限」与「公司数上限」两道闸。
    # 顺序不能反：先截公司数会把配对好的公司挤掉（见上方 docstring 的坑）。
    groups, used, companies_taken = [], 0, 0
    cap = caps["per_segment_per_round"]
    for bucket in (paired, dual, single):
        for grp in bucket:
            if used + len(grp) > cap:
                continue
            if max_companies is not None and companies_taken >= max_companies:
                break
            groups.append(grp)
            used += len(grp)
            companies_taken += 1
    return groups, {"companies_found": len(by_org),
                    "paired": len(paired), "dual_role": len(dual),
                    "single_side": len(single),
                    "companies_taken": companies_taken,
                    "max_companies": max_companies,
                    "rows_after_cap": used, "cap": cap}


# --------------------------------------------------------------------------
# 招聘公司反查清单
# --------------------------------------------------------------------------

def read_backfill(path):
    """data/apollo_backfill.csv —— LinkedIn 周批扫写入的在招公司。

    约定列：company,segment,source_url,hiring_keyword,seen_date
    （playbook scan_linkedin_weekly.md 的字段映射表里逐字写了同一组列名。）
    """
    if not os.path.exists(path):
        return [], "文件不存在（LinkedIn 周批扫尚未跑过，属预期状态）"
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for rec in csv.DictReader(fh):
            company = (rec.get("company") or "").strip()
            if company:
                rows.append({"company": company,
                             "segment": (rec.get("segment") or "").strip(),
                             "source_url": (rec.get("source_url") or "").strip(),
                             "hiring_keyword": (rec.get("hiring_keyword") or "").strip()})
    return rows, None


# --------------------------------------------------------------------------
# 写水箱
# --------------------------------------------------------------------------

def pipeline_props(row, now_iso, today, note_prefix):
    """水箱字段映射。字段名逐字取自 Phase 0 核对清单，一个字都不许改。"""
    return {
        "人名": sc.p_title(row["人名"]),
        "公司": sc.p_text(row["公司"]),
        "角色标签": sc.p_select(row["角色标签"]),
        "segment": sc.p_select(row["segment"]),
        "状态": sc.p_select("inbox"),
        "下一步动作": sc.p_text(row.get("下一步动作") or ""),
        "触达时限起算": sc.p_date(now_iso),
        "信号类型": sc.p_select("signal"),
        "信号原文": sc.p_text("{}｜{} @ {}｜非原文引用：Apollo 名单命中，"
                              "本行来源是名单条件而非本人发言".format(
                                  note_prefix, row.get("title") or "(无 title)",
                                  row["公司"] or "(无公司)")),
        "来源链接": sc.p_url(row["来源链接"]),
        "入箱日期": sc.p_date(today),
        "来源": sc.p_select("Apollo"),
    }


def main():
    parser = sc.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--segments", nargs="+", default=None,
                        help="只跑指定 segment，默认全部")
    parser.add_argument("--limit-companies", type=int, default=None,
                        help="每 segment 最多取几家公司，默认读 scan.yaml")
    parser.add_argument("--backfill", action="store_true",
                        help="只跑 data/apollo_backfill.csv 的招聘公司定向反查")
    args = parser.parse_args()
    mode = sc.resolve_mode(args)

    th = ac.load_config("thresholds.yaml")
    try:
        seg_cfg = ac.load_config("segments.yaml")
        scan_cfg = ac.load_config("scan.yaml")
        a_cfg = scan_cfg["apollo"]
        caps = scan_cfg["caps"]
        env = ac.load_env()

        max_companies = args.limit_companies or a_cfg["max_companies_per_segment"]
        seg_keys = args.segments or list(seg_cfg["segments"].keys())
        unknown = [s for s in seg_keys if s not in seg_cfg["segments"]]
        if unknown:
            raise ValueError("segments.yaml 里没有这些 segment：{}".format(unknown))

        backfill_path = os.path.join(ac.REPO, a_cfg["backfill_csv"])
        backfill_rows, backfill_note = read_backfill(backfill_path)

        # ---- 先把请求全部解析出来。缺凭据时这份 plan 就是本次的全部产出 ----
        plan = []
        if not args.backfill:
            for seg in seg_keys:
                body = seg_cfg["segments"][seg]
                titles = body["titles"]
                cond = {"segment_conditions": body["apollo"]}
                plan.append({
                    "kind": "segment_search",
                    "segment": seg,
                    "segment_name": body["name"],
                    "requests": [
                        {"role": ROLE_PAIN,
                         "payload": build_payload(cond, titles["pain_feeler"],
                                                  a_cfg["per_page"])},
                        {"role": ROLE_DM,
                         "payload": build_payload(cond, titles["decision_maker"],
                                                  a_cfg["per_page"])},
                    ],
                })
        for bf in backfill_rows:
            seg = bf["segment"] if bf["segment"] in seg_cfg["segments"] else None
            body = seg_cfg["segments"].get(seg)
            if not body:
                plan.append({"kind": "backfill_skipped", "company": bf["company"],
                             "reason": "CSV 里的 segment 值 {!r} 不在 segments.yaml".format(
                                 bf["segment"])})
                continue
            titles = body["titles"]
            cond = {"segment_conditions": body["apollo"]}
            plan.append({
                "kind": "backfill_search",
                "segment": seg,
                "company": bf["company"],
                "hiring_source": bf["source_url"],
                "requests": [
                    {"role": ROLE_PAIN,
                     "payload": build_payload(cond, titles["pain_feeler"],
                                              a_cfg["per_page"], org_name=bf["company"])},
                    {"role": ROLE_DM,
                     "payload": build_payload(cond, titles["decision_maker"],
                                              a_cfg["per_page"], org_name=bf["company"])},
                ],
            })

        api_key = env.get("APOLLO_API_KEY")
        if not api_key:
            sc.missing_credential(
                SCRIPT, "APOLLO_API_KEY",
                "Apollo API key 不在 .env（Phase 2 spec §凭据前置清单 把它列为必需）。"
                "没有 key 就一条真实名单都拉不到。下面的 plan 是**已解析完成的请求体**"
                "（由 segments.yaml + scan.yaml 推出，可逐字复核），"
                "**不含任何 Apollo 返回数据**——缺凭据就报缺凭据，不造假响应充数。", th,
                extra={
                    "mode": mode,
                    "endpoint": a_cfg["base_url"] + a_cfg["people_search_path"],
                    "auth_header": a_cfg["auth_header"],
                    "segments_planned": seg_keys,
                    "max_companies_per_segment": max_companies,
                    "per_segment_row_cap": caps["per_segment_per_round"],
                    "backfill": {"path": a_cfg["backfill_csv"],
                                 "rows": len(backfill_rows), "note": backfill_note},
                    "plan": plan,
                    "email_reply_polling": a_cfg["email_reply_polling"],
                })

        # ---- 有凭据才会走到这里 ----
        session = requests.Session()
        notion = ac.Notion(env["NOTION_TOKEN"], env["NOTION_VERSION"])
        seen_urls = sc.existing_source_urls(notion.query_all(env["DS_PIPELINE"]))

        now = sc.now_local(th)
        now_iso, today = now.isoformat(timespec="minutes"), now.strftime("%Y-%m-%d")

        all_groups, stats, calls = [], {}, 0
        for entry in plan:
            if entry["kind"] not in ("segment_search", "backfill_search"):
                continue
            people = []
            for req in entry["requests"]:
                body = call_apollo(session, a_cfg["base_url"], a_cfg["people_search_path"],
                                   a_cfg["auth_header"], api_key, req["payload"])
                calls += 1
                for raw in (body.get("people") or []) + (body.get("contacts") or []):
                    people.append(person_record(raw, entry["segment"], req["role"]))
            # 公司数上限交给 group_and_pair 在分组之后施加，这里不做任何预截断。
            limit = max_companies if entry["kind"] == "segment_search" else None
            groups, st = group_and_pair(people, entry["segment"], caps,
                                        a_cfg["single_side_note_template"], limit)
            key = "{}:{}".format(entry["kind"], entry.get("company") or entry["segment"])
            stats[key] = st
            for grp in groups:
                all_groups.append({"entry": key, "segment": entry["segment"], "rows": grp})

        # 去重：按来源链接查水箱，已存在即跳过
        deduped, skipped_dupe = [], []
        for grp in all_groups:
            keep = []
            for row in grp["rows"]:
                if row["来源链接"] and sc.norm_url(row["来源链接"]) in seen_urls:
                    skipped_dupe.append({"人名": row["人名"], "来源链接": row["来源链接"]})
                    continue
                seen_urls.add(sc.norm_url(row["来源链接"]))
                keep.append(row)
            if keep:
                deduped.append(dict(grp, rows=keep))

        written = []
        if mode == "commit":
            for grp in deduped:
                ids = []
                for row in grp["rows"]:
                    page = notion.create_page(env["DS_PIPELINE"], pipeline_props(
                        row, now_iso, today, "Apollo poll {}".format(grp["entry"])))
                    ids.append(page["id"])
                    written.append({"action": "create", "人名": row["人名"],
                                    "角色标签": row["角色标签"], "page_id": page["id"]})
                # 配对是单向 relation → 两边各写一次（Phase 0 差异①）
                if len(ids) == 2:
                    notion.update_page(ids[0], {"配对": sc.p_relation([ids[1]])})
                    notion.update_page(ids[1], {"配对": sc.p_relation([ids[0]])})
                    written.append({"action": "pair", "page_ids": ids,
                                    "note": "单向 relation，两边各写一次"})

        sc.emit(SCRIPT, {
            "script": SCRIPT,
            "mode": mode,
            "status": "ok",
            "wrote_notion": mode == "commit",
            "api_calls": calls,
            "segments": seg_keys,
            "max_companies_per_segment": max_companies,
            "per_segment_row_cap": caps["per_segment_per_round"],
            "group_stats": stats,
            "planned_rows": sum(len(g["rows"]) for g in deduped),
            "skipped_dupe": skipped_dupe,
            "groups": deduped,
            "written": written,
            "backfill": {"path": a_cfg["backfill_csv"], "rows": len(backfill_rows),
                         "note": backfill_note},
            "email_reply_polling": a_cfg["email_reply_polling"],
        }, th)
        return sc.EXIT_OK

    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
