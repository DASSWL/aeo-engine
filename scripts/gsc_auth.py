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
"""

import os
import sys
from urllib.parse import urlencode

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aeo_common as ac      # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
# Desktop app 用的本地重定向。不起本地服务器——手动把 code 贴回来更简单，
# 而且这脚本一辈子只跑一次，不值得为它引一个 http server。
REDIRECT = "urn:ietf:wg:oauth:2.0:oob"


def main():
    env = ac.load_env()
    missing = [k for k in ("GSC_CLIENT_ID", "GSC_CLIENT_SECRET") if not env.get(k)]
    if missing:
        print("缺 {}。先按本文件开头的 1–5 步在 Google Cloud Console 里拿到，"
              "写进 .env 再跑。".format(" / ".join(missing)), file=sys.stderr)
        return 2

    url = "{}?{}".format(AUTH_URL, urlencode({
        "client_id": env["GSC_CLIENT_ID"],
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        # 这两个一起给才会返回 refresh_token。少任何一个都只给 access_token，
        # 一小时后过期，脚本第二天就跑不了了。
        "access_type": "offline",
        "prompt": "consent",
    }))

    print("\n① 用浏览器打开下面这个地址，用**有 vivu.ai Search Console 权限的那个 Google 账号**授权：\n")
    print(url)
    print("\n② 授权后页面会给一串 code，粘贴到这里：")
    code = input("code = ").strip()
    if not code:
        print("没有输入 code，中止。未做任何事。", file=sys.stderr)
        return 1

    resp = requests.post(TOKEN_URL, data={
        "code": code,
        "client_id": env["GSC_CLIENT_ID"],
        "client_secret": env["GSC_CLIENT_SECRET"],
        "redirect_uri": REDIRECT,
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
