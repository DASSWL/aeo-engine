#!/usr/bin/env python3
"""AEO Engine —— 一次性：换出 Google Ads 的 refresh_token。

只跑一次。跑完把它打印的 refresh_token 贴进 .env，之后 keyword_volume.py --source api
自己续期。与 gsc_auth.py 同一形态（loopback 回跳，OOB 已被 Google 封杀），
scope 换成 Google Ads。

⚠️ 与 gsc_auth 同一条纪律：不写日志、不落 outbox、不自动改 .env。
   凭据只打印到终端，贴哪里由真人决定。

前置（Google Cloud Console，可以直接复用 GSC 那个项目和 OAuth client）：
  1. 在建 GSC client 的那个项目里，「API 和服务 → 库」启用 **Google Ads API**
  2. OAuth 同意屏的 scope 不用预先登记（Desktop app + Test user 模式下请求时带上即可）
  3. 可以直接用 GSC 的 client：.env 里 GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET
     填与 GSC_CLIENT_ID / GSC_CLIENT_SECRET 相同的值，也可以另建一个 Desktop client
  4. GOOGLE_ADS_CUSTOMER_ID 填 Google Ads 账号 ID（如 215-156-2899，带不带连字符都行）
  5. 若该账号经 MCC（经理账号）访问，另配 GOOGLE_ADS_LOGIN_CUSTOMER_ID=<MCC 账号 ID>

然后：
    python3 scripts/ads_auth.py

⚠️ 授权账号必须是**对该 Google Ads 账号有访问权限**的 Google 账号——
   和 GSC 用的是不是同一个账号无关，看的是 Google Ads 后台的权限。
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/adwords"
TIMEOUT_SECONDS = 300


class _CodeCatcher(BaseHTTPRequestHandler):
    code = None
    error = None

    def do_GET(self):  # noqa: N802
        qs = parse_qs(urlparse(self.path).query)
        cls = type(self)
        if "code" in qs:
            cls.code = qs["code"][0]
            msg = "授权完成，可以关掉这个页面，回到终端。"
        elif "error" in qs:
            cls.error = qs["error"][0]
            msg = "授权失败：{}。回终端看提示。".format(cls.error)
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = ("<html><head><meta charset='utf-8'></head>"
                "<body style='font-family:sans-serif;padding:2em'>"
                "<h3>{}</h3></body></html>").format(msg).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    env = ac.load_env()
    missing = [k for k in ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET")
               if not env.get(k)]
    if missing:
        print("缺 {}。可直接复用 GSC 的 client（值抄 GSC_CLIENT_ID / GSC_CLIENT_SECRET），"
              "但要先在同一个 Cloud 项目里启用 Google Ads API。写进 .env 再跑。"
              .format(" / ".join(missing)), file=sys.stderr)
        return 2

    server = HTTPServer(("127.0.0.1", 0), _CodeCatcher)
    port = server.server_address[1]
    redirect = "http://127.0.0.1:{}".format(port)

    url = "{}?{}".format(AUTH_URL, urlencode({
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }))

    print("\n① 用浏览器打开下面这个地址，用**对 Google Ads 账号有权限的那个 Google 账号**授权：\n")
    print(url)
    print("\n② 授权后浏览器会自动回跳到本机（127.0.0.1:{}），这里等它 {} 秒……".format(
        port, TIMEOUT_SECONDS))

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    waited = 0
    while _CodeCatcher.code is None and _CodeCatcher.error is None \
            and waited < TIMEOUT_SECONDS:
        time.sleep(1)
        waited += 1
    server.shutdown()

    if _CodeCatcher.error:
        print("Google 返回错误：{}。常见原因：账号没加进 OAuth 同意屏的 Test users。"
              .format(_CodeCatcher.error), file=sys.stderr)
        return 1
    if _CodeCatcher.code is None:
        print("等了 {} 秒没收到回跳。确认浏览器和本脚本在同一台机器上。".format(
            TIMEOUT_SECONDS), file=sys.stderr)
        return 1

    resp = requests.post(TOKEN_URL, data={
        "code": _CodeCatcher.code,
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }, timeout=30)
    if resp.status_code >= 400:
        print("换 token 失败 HTTP {}：{}".format(resp.status_code, resp.text[:400]),
              file=sys.stderr)
        return 1

    rt = resp.json().get("refresh_token")
    if not rt:
        print("响应里没有 refresh_token。多半是之前已授权过——"
              "去 https://myaccount.google.com/permissions 撤销后重跑。",
              file=sys.stderr)
        return 1

    print("\n✅ 拿到了。把下面这行加进 .env（**不要**贴进任何聊天窗口或提交进 git）：\n")
    print("GOOGLE_ADS_REFRESH_TOKEN={}".format(rt))
    print("\n还要确认 .env 里有 GOOGLE_ADS_CUSTOMER_ID=<Ads 账号 ID>，然后跑：")
    print("python3 scripts/keyword_volume.py --source api   （dry-run，不写库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
