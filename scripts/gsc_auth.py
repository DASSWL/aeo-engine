#!/usr/bin/env python3
"""AEO Engine —— 一次性：换出 Search Console 的 refresh_token。

只跑一次。跑完把它打印的 refresh_token 贴进 .env，之后 gsc_queries.py 自己续期。

⚠️ 本脚本刻意**不写任何日志、不落 outbox、不自动改 .env**：
   它的产出是一个长期有效的凭据。经 sc.emit 会同时落 logs/，
   而 logs/ 虽然 gitignore 了，仍然是明文躺在磁盘上的第二份副本。
   凭据只打印到终端，贴哪里由真人决定。

前置（这几步在 Google Cloud Console 里做，脚本代不了）：
  1. 建或选一个项目
  2. 启用 **Google Search Console API**
  3. OAuth 同意屏：类型选 External，把自己加进 Test users，
     scope 加 `https://www.googleapis.com/auth/webmasters.readonly`
  4. 凭据 → 建 OAuth client ID → 类型选 **Desktop app**
  5. 把拿到的 client_id / client_secret 写进 .env 的
     `GSC_CLIENT_ID` / `GSC_CLIENT_SECRET`

然后：
    python3 scripts/gsc_auth.py

历史：首版用 `urn:ietf:wg:oauth:2.0:oob` 重定向，Google 2023-02 起全面封杀
该方式（授权页直接报「Access blocked: request is invalid」，跟凭据对不对无关）。
2026-08-05 实测踩到，改为 Google 现行推荐的 loopback 方式：起一个本机一次性
HTTP 服务收 code，浏览器授权完自动回跳，不需要手动贴 code。
Desktop app 类型的 client 允许 loopback 上任意端口，无需在 Console 里登记。
"""

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TIMEOUT_SECONDS = 300


class _CodeCatcher(BaseHTTPRequestHandler):
    """收一次 Google 的回跳，把 code 存下来，给浏览器回一句人话。"""

    code = None
    error = None

    def do_GET(self):  # noqa: N802（http.server 的命名约定）
        qs = parse_qs(urlparse(self.path).query)
        cls = type(self)
        if "code" in qs:
            cls.code = qs["code"][0]
            msg = "授权完成，可以关掉这个页面，回到终端。"
        elif "error" in qs:
            cls.error = qs["error"][0]
            msg = "授权失败：{}。回终端看提示。".format(cls.error)
        else:
            # favicon 之类的杂请求，不算数
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

    def log_message(self, *args):  # 静音默认的请求日志
        pass


def main():
    env = ac.load_env()
    missing = [k for k in ("GSC_CLIENT_ID", "GSC_CLIENT_SECRET") if not env.get(k)]
    if missing:
        print("缺 {}。先按本文件开头的 1–5 步在 Google Cloud Console 里拿到，"
              "写进 .env 再跑。".format(" / ".join(missing)), file=sys.stderr)
        return 2

    # 端口 0 = 让系统挑一个空闲端口。Desktop app client 对 loopback 端口不做校验。
    server = HTTPServer(("127.0.0.1", 0), _CodeCatcher)
    port = server.server_address[1]
    redirect = "http://127.0.0.1:{}".format(port)

    url = "{}?{}".format(AUTH_URL, urlencode({
        "client_id": env["GSC_CLIENT_ID"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        # 这两个一起给才会返回 refresh_token。少任何一个都只给 access_token，
        # 一小时后过期，脚本第二天就跑不了了。
        "access_type": "offline",
        "prompt": "consent",
    }))

    print("\n① 用浏览器打开下面这个地址，用**有 vivu.ai Search Console 权限的那个 Google 账号**授权：\n")
    print(url)
    print("\n② 授权后浏览器会自动回跳到本机（127.0.0.1:{}），这里等它 {} 秒……".format(
        port, TIMEOUT_SECONDS))

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    import time
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
        print("等了 {} 秒没收到回跳。确认浏览器和本脚本在同一台机器上；"
              "如果不在，换成在这台机器的浏览器里打开那个地址。".format(TIMEOUT_SECONDS),
              file=sys.stderr)
        return 1

    resp = requests.post(TOKEN_URL, data={
        "code": _CodeCatcher.code,
        "client_id": env["GSC_CLIENT_ID"],
        "client_secret": env["GSC_CLIENT_SECRET"],
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }, timeout=30)
    if resp.status_code >= 400:
        print("换 token 失败 HTTP {}：{}".format(resp.status_code, resp.text[:400]),
              file=sys.stderr)
        return 1

    body = resp.json()
    rt = body.get("refresh_token")
    if not rt:
        print("响应里没有 refresh_token。多半是这个账号之前已经授权过——"
              "去 https://myaccount.google.com/permissions 撤销该应用后重跑，"
              "或确认 access_type=offline 与 prompt=consent 都在。",
              file=sys.stderr)
        return 1

    print("\n✅ 拿到了。把下面这行加进 .env（**不要**贴进任何聊天窗口或提交进 git）：\n")
    print("GSC_REFRESH_TOKEN={}".format(rt))
    print("\n然后跑：python3 scripts/gsc_queries.py   （dry-run，不写库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
