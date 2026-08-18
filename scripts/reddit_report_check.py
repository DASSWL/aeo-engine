#!/usr/bin/env python3
"""Reddit 爬取报告体检 —— 外部爬虫交付物 vs docs/reddit_report_spec.md。

出处：2026-08-17 Shawn 拍板「我的工具每天爬 reddit,你只负责分析报告」。

这个脚本是**两边共用的合同**：爬虫作者拿它当验收条件,分析侧拿它当准入闸。
它只读文件、不碰网络、不写 Notion。

退出码:0 = 可用;1 = 报告有硬伤,不进分析;2 = 报告根本不在。

刻意不做的事:不修报告、不补默认值、不跳过坏行。
报告是分析侧唯一的输入,悄悄修一条等于伪造一次观察。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402
import aeo_scan as sc        # noqa: E402

SCRIPT = "reddit_report_check"

INBOX_DIR = os.path.join(ac.REPO, "inbox", "reddit")
SCHEMA_VERSION = 1

RUN_STATUS = {"ok", "partial", "failed"}
QUERY_STATUS = {"ok", "failed", "skipped"}
POST_REQUIRED = ("id", "permalink", "subreddit", "found_by", "title",
                 "selftext", "author", "created_utc", "state", "comments")
STATE_REQUIRED = ("deleted", "removed", "locked")
# 规范 §四:permalink 必须已规范成这个形式,它是水箱的去重键
PERMALINK_RE = re.compile(
    r"^https://www\.reddit\.com/r/[A-Za-z0-9_]+/comments/[a-z0-9]+/[^?#]*$")
# 报告里出现这些字样多半是把凭据写进去了(规范 §四 明确禁止)
SECRET_RE = re.compile(
    r"(client_secret|refresh_token|bearer\s+[A-Za-z0-9._-]{20,}|"
    r"api[_-]?key\"?\s*[:=]\s*\"[^\"]{16,})", re.I)


def expected_matrix():
    """segments.yaml 展开成期望的 (segment, subreddit, keyword) 组合集合。

    覆盖检查的整个价值在这里:少一个组合而报告不说,分析侧就会把
    「没扫」当成「扫了 0 条」——2026-08-15 断料六天正是栽在这个区分上。
    """
    segs = ac.load_config("segments.yaml")["segments"]
    want = set()
    for name, seg in segs.items():
        for sub in (seg.get("subreddits") or []):
            for kw in (seg.get("linkedin_keywords") or []):
                want.add((name, sub, kw))
    return want


def latest_report(dirpath):
    if not os.path.isdir(dirpath):
        return None
    files = [f for f in os.listdir(dirpath)
             if f.startswith("reddit_scan_") and f.endswith((".json", ".json.gz"))]
    return os.path.join(dirpath, sorted(files)[-1]) if files else None


def load_report(path):
    if path.endswith(".gz"):
        import gzip
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check(rep, want):
    """→ (硬伤 list, 提醒 list, 统计 dict)。硬伤非空即拒收。"""
    bad, warn = [], []

    if rep.get("schema_version") != SCHEMA_VERSION:
        bad.append("schema_version 应为 {},实际 {!r}".format(
            SCHEMA_VERSION, rep.get("schema_version")))

    run = rep.get("run") or {}
    if not isinstance(run, dict) or not run:
        bad.append("缺 run 段")
    else:
        if not run.get("started_at"):
            bad.append("run.started_at 缺失")
        if run.get("status") not in RUN_STATUS:
            bad.append("run.status 应为 {},实际 {!r}".format(
                "/".join(sorted(RUN_STATUS)), run.get("status")))

    queries = rep.get("queries")
    seen, q_ok, q_failed, q_skipped = set(), 0, 0, 0
    if not isinstance(queries, list):
        bad.append("缺 queries 段(整轮失败也必须落,且每个计划组合都要有一行)")
        queries = []
    for i, q in enumerate(queries):
        where = "queries[{}]".format(i)
        seg, sub, kw = q.get("segment"), q.get("subreddit"), q.get("keyword")
        if not (seg and sub and kw):
            bad.append("{} 缺 segment/subreddit/keyword".format(where))
            continue
        seen.add((seg, sub, kw))
        st = q.get("status")
        if st not in QUERY_STATUS:
            bad.append("{} status 应为 {},实际 {!r}".format(
                where, "/".join(sorted(QUERY_STATUS)), st))
        elif st == "ok":
            q_ok += 1
            if not isinstance(q.get("returned"), int):
                bad.append("{} status=ok 必须给 returned(整数,0 也要写)".format(where))
        elif st == "failed":
            q_failed += 1
            if not q.get("error"):
                warn.append("{} status=failed 但没写 error,查不出为什么".format(where))
        else:
            q_skipped += 1
            if not q.get("reason"):
                warn.append("{} status=skipped 但没写 reason".format(where))

    missing = want - seen
    if missing:
        bad.append("覆盖矩阵缺 {} 个组合(必须有行,失败/跳过也要有):{}{}".format(
            len(missing),
            "; ".join("{}|{}|{}".format(*m) for m in sorted(missing)[:5]),
            " …" if len(missing) > 5 else ""))
    extra = seen - want
    if extra:
        warn.append("{} 个组合不在 segments.yaml 矩阵里(不拦,但请确认不是笔误):{}".format(
            len(extra), "; ".join("{}|{}|{}".format(*m) for m in sorted(extra)[:3])))
    if run.get("status") == "ok" and (q_failed or q_skipped):
        bad.append("run.status=ok 但有 {} 个 failed / {} 个 skipped —— "
                   "应为 partial".format(q_failed, q_skipped))

    posts = rep.get("posts")
    if not isinstance(posts, list):
        bad.append("缺 posts 段(0 条也要给空数组)")
        posts = []
    ids, thin_comments, deleted = set(), 0, 0
    for i, p in enumerate(posts):
        where = "posts[{}]".format(p.get("id") or i)
        for f in POST_REQUIRED:
            if f not in p:
                bad.append("{} 缺必填字段 {}".format(where, f))
        pl = p.get("permalink") or ""
        if pl and not PERMALINK_RE.match(pl):
            bad.append("{} permalink 不是规范形式(去 old./np./短链/参数/统一尾斜杠):{}"
                       .format(where, pl[:80]))
        fb = p.get("found_by")
        if not isinstance(fb, list) or not fb:
            bad.append("{} found_by 必须是非空数组 —— 同一 sub 挂在两个 segment 下"
                       "(r/editors 在 A 和 E),只留一条就判不出归哪段".format(where))
        else:
            for e in fb:
                key = (e.get("segment"), p.get("subreddit"), e.get("keyword"))
                if key not in seen:
                    warn.append("{} found_by {} 在 queries 段里找不到对应行"
                                .format(where, key))
        st = p.get("state") or {}
        for f in STATE_REQUIRED:
            if f not in st:
                bad.append("{} state.{} 缺失(已删/已锁只标记不删除,排除是分析侧的活)"
                           .format(where, f))
        if st.get("deleted") or st.get("removed"):
            deleted += 1
        cs = p.get("comments")
        if isinstance(cs, list) and len(cs) < 10 and not p.get("comments_truncated"):
            thin_comments += 1
        if p.get("id"):
            ids.add(p["id"])

    if SECRET_RE.search(json.dumps(rep, ensure_ascii=False)):
        bad.append("报告里出现疑似凭据字样 —— 规范 §四 明确禁止,请清掉后重出")
    if not posts and run.get("status") == "ok":
        warn.append("posts 为 0 条而 run.status=ok。可能真的没有,也可能筛过头了")
    if thin_comments:
        warn.append("{} 条帖子首层评论不足 10 条且没标 comments_truncated —— "
                    "帖型判定的范围是「标题或首层评论」,评论少会影响判定".format(thin_comments))

    stats = {"queries_rows": len(queries), "queries_ok": q_ok,
             "queries_failed": q_failed, "queries_skipped": q_skipped,
             "matrix_expected": len(want), "matrix_covered": len(want & seen),
             "posts": len(posts), "posts_unique": len(ids),
             "posts_deleted_or_removed": deleted}
    return bad, warn, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", default=None,
                    help="报告路径。不给就取 inbox/reddit/ 里日期最新的一份")
    args = ap.parse_args()

    try:
        th = ac.load_config("thresholds.yaml")
        path = args.report or latest_report(INBOX_DIR)
        if not path or not os.path.exists(path):
            sc.emit(SCRIPT, {"script": SCRIPT, "status": "no_report",
                             "looked_in": INBOX_DIR,
                             "hint": "爬虫应把 reddit_scan_YYYY-MM-DD.json 落在这里"
                                     "(先写 .tmp 再 rename)"}, th)
            return 2

        try:
            rep = load_report(path)
        except (ValueError, UnicodeDecodeError) as exc:
            sc.emit(SCRIPT, {"script": SCRIPT, "status": "unreadable",
                             "report": path, "error": str(exc)}, th)
            return 1

        bad, warn, stats = check(rep, expected_matrix())
        result = {"script": SCRIPT, "report": path,
                  "status": "rejected" if bad else "accepted",
                  "checked_at": datetime.now().isoformat(timespec="seconds"),
                  "stats": stats, "blocking": bad, "warnings": warn}
        sc.emit(SCRIPT, result, th)
        return 1 if bad else 0

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ac.fail(SCRIPT, exc)


if __name__ == "__main__":
    sys.exit(main())
