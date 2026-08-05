#!/usr/bin/env python3
"""读取 WithEcho 开放接口数据，输出 JSON 到 stdout。

  python fetch.py daily [--limit N] [--cursor C] [--all]
  python fetch.py daily-detail --event-id ID
  python fetch.py diary [--from YYYY-MM-DD --to YYYY-MM-DD | --limit N --cursor C]
  python fetch.py diary-detail --diary-id ID
  python fetch.py muse [--limit N] [--cursor C] [--all]

401 自动刷新令牌重试一次；仍失败则提示重新登录。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402

MAX_PAGES = 50  # --all 翻页保险上限


def get(path: str, params: dict, _retried=False) -> dict:
    creds = auth.ensure_fresh()
    query = {k: v for k, v in params.items() if v not in (None, "")}
    url = auth.API_BASE + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + creds["access_token"]})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 401 and not _retried:
            auth.refresh()
            return get(path, params, _retried=True)
        try:
            err = json.load(e)
        except json.JSONDecodeError:
            err = {"error": "http_%d" % e.code}
        raise auth.die(err.get("error", "request_error"), err.get("error_description", ""))
    except urllib.error.URLError as e:
        raise auth.die("network_error", str(e.reason))


def paged(path: str, list_key: str, limit: int, cursor: str, fetch_all: bool) -> dict:
    """游标翻页；fetch_all 时聚合所有页到一个列表。"""
    if not fetch_all:
        return get(path, {"limit": limit, "cursor": cursor})
    items, pages = [], 0
    while pages < MAX_PAGES:
        resp = get(path, {"limit": 50, "cursor": cursor})
        items.extend(resp.get(list_key, []))
        cursor = resp.get("next_cursor", "")
        pages += 1
        if not cursor:
            break
    return {list_key: items, "next_cursor": cursor}


def emit(data: dict):
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()


def main():
    p = argparse.ArgumentParser(description="读取 WithEcho 数据")
    sub = p.add_subparsers(dest="cmd", required=True)

    daily = sub.add_parser("daily", help="日常事件列表")
    daily.add_argument("--limit", type=int, default=20)
    daily.add_argument("--cursor", default="")
    daily.add_argument("--all", action="store_true", help="自动翻页拉全部")

    dd = sub.add_parser("daily-detail", help="事件详情（正文+洞察卡片）")
    dd.add_argument("--event-id", required=True)

    diary = sub.add_parser("diary", help="日记列表")
    diary.add_argument("--from", dest="date_from", default="", metavar="YYYY-MM-DD")
    diary.add_argument("--to", dest="date_to", default="", metavar="YYYY-MM-DD")
    diary.add_argument("--limit", type=int, default=20)
    diary.add_argument("--cursor", default="")

    dy = sub.add_parser("diary-detail", help="日记正文")
    dy.add_argument("--diary-id", required=True)

    muse = sub.add_parser("muse", help="碎碎念列表（自带正文）")
    muse.add_argument("--limit", type=int, default=20)
    muse.add_argument("--cursor", default="")
    muse.add_argument("--all", action="store_true", help="自动翻页拉全部")

    args = p.parse_args()

    if args.cmd == "daily":
        emit(paged("/open/daily", "events", args.limit, args.cursor, args.all))
    elif args.cmd == "daily-detail":
        emit(get("/open/daily/detail", {"event_id": args.event_id}))
    elif args.cmd == "diary":
        if args.date_from or args.date_to:
            emit(get("/open/diaries", {"from": args.date_from, "to": args.date_to}))
        else:
            emit(get("/open/diaries", {"limit": args.limit, "cursor": args.cursor}))
    elif args.cmd == "diary-detail":
        emit(get("/open/diaries/detail", {"diary_id": args.diary_id}))
    elif args.cmd == "muse":
        emit(paged("/open/muses", "muses", args.limit, args.cursor, args.all))


if __name__ == "__main__":
    main()
