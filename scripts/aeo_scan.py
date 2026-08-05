"""AEO Engine · Phase 2 三个接入脚本的共用底座。

与 aeo_common 的分工：
  aeo_common —— Phase 1 建立的底座（.env / config 读取、Notion 客户端、周窗口）。本文件不重复。
  aeo_scan   —— Phase 2 才需要的东西：dry-run 开关、Notion property 构造、
                去重、运行结果的落盘格式。

三条纪律（三个脚本共用，违反即为不合格）：
  1. **默认 dry-run。** 不带参数跑 = 只算不写。写库必须显式 --commit。
     出处：Phase 0 的六条测试记录清理折腾过一轮，不再允许任何「顺手写进真库」。
  2. **缺凭据就报缺凭据。** 不造假响应、不塞示例数据充数，以退出码 2 退出。
  3. **阈值与规则一律从 config/ 读。** 本文件与三个脚本内不出现任何业务数字字面量。
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac  # noqa: E402

# 退出码约定（三个脚本一致，供 shell / 定时任务判断）
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_MISSING_CREDENTIAL = 2


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------

def base_parser(description):
    """--dry-run 与 --commit 互斥，两个都不给时默认 dry-run。"""
    p = argparse.ArgumentParser(description=description)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="只计算与输出，不写 Notion（默认）")
    g.add_argument("--commit", dest="commit", action="store_true",
                   help="真正写入 Notion。不给这个参数就绝不写库")
    return p


def resolve_mode(args):
    return "commit" if getattr(args, "commit", False) else "dry-run"


# --------------------------------------------------------------------------
# Notion property 构造
#
# 字段名一律由调用方按 Phase 0 核对清单逐字传入，本文件不持有任何字段名。
# --------------------------------------------------------------------------

def p_title(text):
    return {"title": [{"type": "text", "text": {"content": text or ""}}]}


def p_text(text):
    if text is None or text == "":
        return {"rich_text": []}
    # Notion 单个 rich_text 上限 2000 字符，超了整条请求会 400。
    return {"rich_text": [{"type": "text", "text": {"content": str(text)[:2000]}}]}


def p_select(name):
    return {"select": None} if not name else {"select": {"name": name}}


def p_number(value):
    return {"number": None if value is None else float(value)}


def p_url(url):
    return {"url": url or None}


def p_checkbox(flag):
    return {"checkbox": bool(flag)}


def p_date(value):
    """value: 'YYYY-MM-DD' 或 ISO 带时区的字符串。"""
    return {"date": None if not value else {"start": value}}


def p_relation(page_ids):
    return {"relation": [{"id": i} for i in (page_ids or [])]}


# --------------------------------------------------------------------------
# 时间
# --------------------------------------------------------------------------

def now_local(cfg_thresholds):
    """本地时刻。时区取 thresholds.yaml 的 week_window.timezone，不硬编码。"""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(cfg_thresholds["week_window"]["timezone"]))


def today_str(cfg_thresholds):
    return now_local(cfg_thresholds).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# 去重
# --------------------------------------------------------------------------

def norm_query(text):
    """query 文本的去重键：去首尾空白、压缩内部空白、转小写。"""
    return " ".join((text or "").split()).lower()


def norm_url(url):
    """来源链接的去重键：去协议差异、去尾斜杠、去 query 串与锚点、转小写。

    LinkedIn 与 Reddit 的同一条内容会带不同的追踪参数，不归一化会重复入箱。
    """
    u = (url or "").strip().lower()
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
    if u.startswith("www."):
        u = u[4:]
    for sep in ("?", "#"):
        if sep in u:
            u = u.split(sep, 1)[0]
    return u.rstrip("/")


def existing_query_texts(rows):
    """Query 库现有 query 文本的归一化集合。字段名逐字：query 文本。"""
    out = set()
    for r in rows:
        t = ac.title_text(r.get("properties", {}), "query 文本")
        if t:
            out.add(norm_query(t))
    return out


def existing_source_urls(rows):
    """水箱现有来源链接的归一化集合。字段名逐字：来源链接。"""
    out = set()
    for r in rows:
        prop = r.get("properties", {}).get("来源链接") or {}
        if prop.get("type") == "url" and prop.get("url"):
            out.add(norm_url(prop["url"]))
    return out


# --------------------------------------------------------------------------
# 结果落盘
# --------------------------------------------------------------------------

def emit(script_name, result, cfg_thresholds):
    """JSON 打到 stdout，同时留档 logs/<script>_<date>.json。

    stdout 与留档内容完全一致——定时任务读 stdout，人复查读 logs，两者不能有出入。
    """
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    path = os.path.join(
        ac.LOGS_DIR, "{}_{}.json".format(script_name, today_str(cfg_thresholds)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return path


def missing_credential(script_name, key, why, cfg_thresholds, extra=None):
    """凭据缺失的统一出口。

    刻意不降级、不静默跳过、更不造假数据：以退出码 2 退出，让缺失被看见。
    """
    result = {
        "script": script_name,
        "status": "missing_credential",
        "missing": key,
        "explain": why,
        "wrote_notion": False,
    }
    if extra:
        result.update(extra)
    emit(script_name, result, cfg_thresholds)
    sys.exit(EXIT_MISSING_CREDENTIAL)
