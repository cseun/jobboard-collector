#!/usr/bin/env python3
"""내 PC에서 서버의 '빈칸 목록'을 받고, 찾은 값을 다시 채워 넣는 도우미(표준 라이브러리만 사용).
서버 접속은 SSH 터널(예: ssh -L 18080:localhost:80 서버)을 켠 뒤 http://localhost:18080 으로 합니다.

  python tools/fill_gaps_client.py list --fields 필수,우대 --limit 20 > gaps.json     # 빈칸 목록(요약은 맨 위)
  python tools/fill_gaps_client.py fill fills.json                                     # 찾은 값 넣기

fills.json 모양:  [{"id": 123, "fields": {"필수": ["C++ 3년"], "우대": "ROS2", "등록일": "2026-11-20"}, "by": "내PC"}]
- 기본은 '빈칸만' 채웁니다. 이미 값이 있으면 건너뜀(서버가 이유를 알려 줌). 덮어쓰려면 항목에 "overwrite": true.
- 비밀번호는 환경변수 JOBBOARD_PASSWORD 또는 입력창(화면에 안 보임)으로 받습니다. 파일에 저장하지 않습니다.
"""
import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def login(base: str, email: str) -> str:
    pw = os.environ.get("JOBBOARD_PASSWORD") or getpass.getpass(f"{email} 비밀번호: ")
    req = urllib.request.Request(base + "/api/auth/login", data=json.dumps({"email": email, "password": pw}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        cookies = r.headers.get_all("Set-Cookie") or []
    for c in cookies:                       # Secure 표시가 있어도 터널(http)에서 쓸 수 있게 값만 꺼내 직접 보냄
        if c.startswith("session="):
            return c.split(";", 1)[0]
    sys.exit("로그인 쿠키를 받지 못했어요")


def call(base: str, cookie: str, method: str, path: str, body=None):
    req = urllib.request.Request(base + path, method=method, headers={"Cookie": cookie, "Content-Type": "application/json"},
                                 data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode(errors="replace")[:300]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("JOBBOARD_URL", "http://localhost:18080"))
    ap.add_argument("--email", default=os.environ.get("JOBBOARD_EMAIL"), required=not os.environ.get("JOBBOARD_EMAIL"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--fields", default="")
    ls.add_argument("--limit", type=int, default=50)
    ls.add_argument("--offset", type=int, default=0)
    fl = sub.add_parser("fill")
    fl.add_argument("file")
    a = ap.parse_args(argv)
    cookie = login(a.base.rstrip("/"), a.email)
    base = a.base.rstrip("/")
    if a.cmd == "list":
        q = urllib.parse.urlencode({k: v for k, v in {"fields": a.fields, "limit": a.limit, "offset": a.offset}.items() if v != ""})
        print(json.dumps(call(base, cookie, "GET", "/api/admin/gaps?" + q), ensure_ascii=False, indent=2))
        return 0
    for item in json.load(open(a.file, encoding="utf-8")):
        res = call(base, cookie, "POST", f"/api/admin/jobs/{int(item['id'])}/fill",
                   {"fields": item["fields"], "by": item.get("by", "pc"), "overwrite": bool(item.get("overwrite"))})
        print(item["id"], json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
