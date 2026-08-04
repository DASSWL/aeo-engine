"""AEO Engine —— metrics.py 与 sla_check.py 的共用底座。

只放三类东西：.env 与 config 的读取、Notion API 客户端、日期与周窗口口径。
这里不做任何业务计算，也不含任何阈值——阈值全部来自 config/。
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(REPO, "config")
DATA_DIR = os.path.join(REPO, "data")
LOGS_DIR = os.path.join(REPO, "logs")
OUTBOX_DIR = os.path.join(REPO, "outbox")

# .gitignore 排除了这三个目录，新 clone 出来不存在，启动时兜住
for _d in (DATA_DIR, LOGS_DIR, OUTBOX_DIR):
    os.makedirs(_d, exist_ok=True)


# --------------------------------------------------------------------------
# 配置读取
# --------------------------------------------------------------------------

def load_env():
    """读仓库根目录 .env。不用第三方库，避免多一个依赖。"""
    path = os.path.join(REPO, ".env")
    if not os.path.exists(path):
        raise RuntimeError(".env 不存在：{}".format(path))
    env = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def load_config(name):
    with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------
# 周窗口
# --------------------------------------------------------------------------

def week_window(cfg_thresholds, now=None):
    """上周五 15:00 → 本周五 15:00（America/Los_Angeles）。

    锚点全部来自 config/thresholds.yaml 的 week_window 节，不硬编码。
    窗口右端取「不晚于当前时刻的最近一个锚点」——周五 15:00 跑时右端即此刻。
    """
    w = cfg_thresholds["week_window"]
    tz = ZoneInfo(w["timezone"])
    now = now.astimezone(tz) if now else datetime.now(tz)

    anchor = now.replace(hour=w["anchor_hour"], minute=w["anchor_minute"],
                         second=0, microsecond=0)
    # 回退到最近一个 anchor_weekday
    delta = (anchor.weekday() - w["anchor_weekday"]) % 7
    anchor = anchor - timedelta(days=delta)
    if anchor > now:
        anchor -= timedelta(days=7)

    return {
        "start": anchor - timedelta(days=7),
        "end": anchor,
        "tz": tz,
        # ISO 周编号取右端所在周
        "label": "{}-{:02d}".format(*anchor.isocalendar()[:2]),
    }


def in_window(dt, window):
    """左闭右开。dt 为 None 时不算命中。"""
    if dt is None:
        return False
    return window["start"] <= dt < window["end"]


# --------------------------------------------------------------------------
# Notion 属性取值
# --------------------------------------------------------------------------

def parse_notion_date(prop, tz):
    """Notion date property → (aware datetime, is_datetime)。

    纯日期（无时间）按当地 00:00 处理——这是把日期放进以小时为界的周窗口时
    唯一无歧义的取法，且与「日期差」判定不冲突。
    """
    if not prop or prop.get("type") != "date" or not prop.get("date"):
        return None, False
    start = prop["date"].get("start")
    if not start:
        return None, False
    has_time = "T" in start
    dt = datetime.fromisoformat(start)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz), has_time


def parse_iso_utc(s, tz):
    """页面级 last_edited_time / created_time（ISO UTC）→ 当地 aware datetime。"""
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(tz)


def select_name(props, field):
    p = props.get(field)
    if not p or p.get("type") != "select" or not p.get("select"):
        return None
    return p["select"].get("name")


def title_text(props, field):
    p = props.get(field)
    if not p or p.get("type") != "title":
        return ""
    return "".join(t.get("plain_text", "") for t in p.get("title", []))


def rich_text(props, field):
    p = props.get(field)
    if not p or p.get("type") != "rich_text":
        return ""
    return "".join(t.get("plain_text", "") for t in p.get("rich_text", []))


def relation_ids(props, field):
    p = props.get(field)
    if not p or p.get("type") != "relation":
        return []
    return [r.get("id") for r in p.get("relation", [])]


def date_prop(props, field, tz):
    return parse_notion_date(props.get(field), tz)[0]


# --------------------------------------------------------------------------
# Notion 客户端
# --------------------------------------------------------------------------

class NotionError(RuntimeError):
    pass


class Notion:
    """只读客户端 + archive。429 退避重试 3 次，其余错误直接抛。

    原则（spec §四）：宁可报错，不可静默输出错数。任何一次读失败都会冒泡到
    调用方，由调用方整体失败并写 outbox 错误消息。
    """

    BASE = "https://api.notion.com/v1"
    MAX_RETRIES = 3

    def __init__(self, token, version):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": "Bearer {}".format(token),
            "Notion-Version": version,
            "Content-Type": "application/json",
        })

    def _request(self, method, path, payload=None):
        url = "{}{}".format(self.BASE, path)
        for attempt in range(self.MAX_RETRIES + 1):
            resp = self.session.request(method, url, json=payload, timeout=30)
            if resp.status_code == 429:
                if attempt == self.MAX_RETRIES:
                    raise NotionError("429 限流，退避重试 {} 次后仍失败：{}".format(
                        self.MAX_RETRIES, path))
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise NotionError("{} {} → HTTP {}：{}".format(
                    method, path, resp.status_code, resp.text[:300]))
            return resp.json()
        raise NotionError("不可达")

    def query_all(self, data_source_id):
        """按 data source 取全量行（spec：按 DS_ ID 查询，不是 DB_ ID）。"""
        rows, cursor = [], None
        while True:
            payload = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            body = self._request(
                "POST", "/data_sources/{}/query".format(data_source_id), payload)
            rows.extend(body.get("results", []))
            if not body.get("has_more"):
                return rows
            cursor = body.get("next_cursor")

    def get_page(self, page_id):
        return self._request("GET", "/pages/{}".format(page_id))

    def archive_page(self, page_id):
        return self._request("PATCH", "/pages/{}".format(page_id),
                             {"archived": True})


# --------------------------------------------------------------------------
# outbox
# --------------------------------------------------------------------------

def write_outbox(filename, content):
    """写待发内容。凭据不出 OpenClaw：脚本只落文件，发送由 sales agent 完成。"""
    path = os.path.join(OUTBOX_DIR, filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def fail(script_name, exc, window_label=None):
    """整体失败：写 outbox 错误消息并以非零码退出。失败本身必须被看见。"""
    stamp = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M %Z")
    body = "\n".join([
        "⚠️ AEO Engine · {} 执行失败".format(script_name),
        "",
        "时间：{}".format(stamp),
        "周窗口：{}".format(window_label or "未确定"),
        "",
        "错误：",
        "{}: {}".format(type(exc).__name__, exc),
        "",
        "本次未产出任何度量结果。请勿按缺失数据做判断。",
    ])
    write_outbox("error_{}_{}.md".format(
        script_name, datetime.now().strftime("%Y%m%d_%H%M%S")), body)
    print(body, file=sys.stderr)
    sys.exit(1)
