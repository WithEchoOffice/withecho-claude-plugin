#!/usr/bin/env python3
"""读取 WithEcho 开放接口数据，输出 JSON 到 stdout。

  python fetch.py daily [--limit N] [--cursor C] [--all]
  python fetch.py daily-detail --event-id ID
  python fetch.py digest --date YYYY-MM-DD
  python fetch.py search --q 关键词 [--limit N]
  python fetch.py diary [--from YYYY-MM-DD --to YYYY-MM-DD | --limit N --cursor C]
  python fetch.py diary-detail --diary-id ID
  python fetch.py muse [--limit N] [--cursor C] [--all]
  python fetch.py tasks [--status S] [--limit N] [--cursor C] [--all]
  python fetch.py task-detail --task-id ID
  python fetch.py reminders [--status S] [--limit N] [--cursor C] [--all]
  python fetch.py asr-files (--date YYYY-MM-DD | --from YYYY-MM-DD --to YYYY-MM-DD)
  python fetch.py asr-export (--filename segments/YYYY/MM/DD/<id>.txt | --date YYYY-MM-DD)

401 自动刷新令牌重试一次；仍失败则提示重新登录。
asr-export 结果永久缓存在 ~/.withecho/asr/<openid>/，命中本地不请求服务器、不扣额度。
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
            auth.refresh(known=creds)
            return get(path, params, _retried=True)
        try:
            err = json.load(e)
        except json.JSONDecodeError:
            err = {"error": "http_%d" % e.code}
        raise auth.die(err.get("error", "request_error"), err.get("error_description", ""))
    except urllib.error.URLError as e:
        raise auth.die("network_error", str(e.reason))


def paged(path: str, list_key: str, limit: int, cursor: str, fetch_all: bool,
          extra: dict = None) -> dict:
    """游标翻页；fetch_all 时聚合所有页到一个列表。"""
    extra = extra or {}
    if not fetch_all:
        return get(path, {"limit": limit, "cursor": cursor, **extra})
    items, pages = [], 0
    while pages < MAX_PAGES:
        resp = get(path, {"limit": 50, "cursor": cursor, **extra})
        items.extend(resp.get(list_key, []))
        cursor = resp.get("next_cursor", "")
        pages += 1
        if not cursor:
            break
    return {list_key: items, "next_cursor": cursor}


def emit(data: dict):
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()


# ---------- ASR 原文本地缓存 ----------
# 每导出一个转写文件都消耗会员月配额（同一文件重复导出照计），所以导出结果永久留在本地
# ~/.withecho/asr/<openid>/<filename>，之后先查本地、没有再向服务器要。按 openid 分目录，
# 换账号登录不会串数据，退出登录也不清（用户自己的数据，保留在本地）。

ASR_CACHE_DIR = os.path.expanduser("~/.withecho/asr")


def asr_cache_path(filename: str) -> str:
    creds = auth.load_credentials() or {}
    openid = creds.get("openid") or "default"
    return os.path.join(ASR_CACHE_DIR, openid, *filename.split("/"))


def asr_cache_read(filename: str):
    try:
        with open(asr_cache_path(filename), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def asr_cache_write(filename: str, content: str):
    path = asr_cache_path(filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def asr_export_one(filename: str) -> dict:
    """单个转写文件：本地命中直接返回（不请求、不扣额度），否则向服务器导出并落盘。"""
    cached = asr_cache_read(filename)
    if cached is not None:
        return {"filename": filename, "source": "local", "content": cached}
    resp = get("/open/asr/export", {"filename": filename})
    asr_cache_write(filename, resp.get("content", ""))
    return {"filename": filename, "source": "server", "content": resp.get("content", ""),
            "quota": resp.get("quota")}


def asr_files(date: str, date_from: str, date_to: str) -> dict:
    params = {"date": date} if date else {"from": date_from, "to": date_to}
    resp = get("/open/asr/files", params)
    for f in resp.get("files", []):
        f["cached"] = os.path.exists(asr_cache_path(f["filename"]))
    return resp


def main():
    p = argparse.ArgumentParser(description="读取 WithEcho 数据")
    sub = p.add_subparsers(dest="cmd", required=True)

    daily = sub.add_parser("daily", help="日常事件列表")
    daily.add_argument("--limit", type=int, default=20)
    daily.add_argument("--cursor", default="")
    daily.add_argument("--all", action="store_true", help="自动翻页拉全部")

    dd = sub.add_parser("daily-detail", help="事件详情（正文+洞察卡片）")
    dd.add_argument("--event-id", required=True)

    dg = sub.add_parser("digest", help="单日聚合：事件+日记正文+碎碎念")
    dg.add_argument("--date", required=True, metavar="YYYY-MM-DD")

    se = sub.add_parser("search", help="跨域检索（洞察卡片/日记/任务）")
    se.add_argument("--q", required=True, help="关键词，1-100 字符")
    se.add_argument("--limit", type=int, default=10, help="每段条数，最大 20")

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

    tasks = sub.add_parser("tasks", help="研究任务列表")
    tasks.add_argument("--status", default="",
                       help="proposed/confirmed/running/completed/failed/cancelled，空为全部")
    tasks.add_argument("--limit", type=int, default=20)
    tasks.add_argument("--cursor", default="")
    tasks.add_argument("--all", action="store_true", help="自动翻页拉全部")

    tk = sub.add_parser("task-detail", help="任务详情（含 Markdown 交付物全文）")
    tk.add_argument("--task-id", required=True)

    rem = sub.add_parser("reminders", help="提醒列表（默认只看生效中的）")
    rem.add_argument("--status", default="",
                     help="默认 active；可传 proposed/triggered/cancelled/expired 或 all")
    rem.add_argument("--limit", type=int, default=20)
    rem.add_argument("--cursor", default="")
    rem.add_argument("--all", action="store_true", help="自动翻页拉全部")

    af = sub.add_parser("asr-files", help="按天列出语音识别原文文件与事件对应关系（不扣额度）")
    af.add_argument("--date", default="", metavar="YYYY-MM-DD")
    af.add_argument("--from", dest="date_from", default="", metavar="YYYY-MM-DD")
    af.add_argument("--to", dest="date_to", default="", metavar="YYYY-MM-DD")

    ae = sub.add_parser("asr-export", help="导出语音识别原文（本地缓存优先，未缓存的每个文件扣 1 次月配额）")
    ae.add_argument("--filename", default="", help="segments/YYYY/MM/DD/<id>.txt，来自 daily-detail 的 transcript_file 或 asr-files")
    ae.add_argument("--date", default="", metavar="YYYY-MM-DD", help="导出当天全部文件（已缓存的不再请求）")

    args = p.parse_args()

    if args.cmd == "daily":
        emit(paged("/open/daily", "events", args.limit, args.cursor, args.all))
    elif args.cmd == "daily-detail":
        emit(get("/open/daily/detail", {"event_id": args.event_id}))
    elif args.cmd == "digest":
        emit(get("/open/digest", {"date": args.date}))
    elif args.cmd == "search":
        emit(get("/open/search", {"q": args.q, "limit": args.limit}))
    elif args.cmd == "diary":
        if args.date_from or args.date_to:
            emit(get("/open/diaries", {"from": args.date_from, "to": args.date_to}))
        else:
            emit(get("/open/diaries", {"limit": args.limit, "cursor": args.cursor}))
    elif args.cmd == "diary-detail":
        emit(get("/open/diaries/detail", {"diary_id": args.diary_id}))
    elif args.cmd == "muse":
        emit(paged("/open/muses", "muses", args.limit, args.cursor, args.all))
    elif args.cmd == "tasks":
        emit(paged("/open/tasks", "tasks", args.limit, args.cursor, args.all,
                   extra={"status": args.status}))
    elif args.cmd == "task-detail":
        emit(get("/open/tasks/detail", {"task_id": args.task_id}))
    elif args.cmd == "reminders":
        emit(paged("/open/reminders", "reminders", args.limit, args.cursor, args.all,
                   extra={"status": args.status}))
    elif args.cmd == "asr-files":
        if not args.date and not (args.date_from and args.date_to):
            raise auth.die("invalid_request", "需要 --date，或 --from 与 --to")
        emit(asr_files(args.date, args.date_from, args.date_to))
    elif args.cmd == "asr-export":
        if bool(args.filename) == bool(args.date):
            raise auth.die("invalid_request", "--filename 与 --date 二选一")
        if args.filename:
            emit(asr_export_one(args.filename))
        else:
            listing = asr_files(args.date, "", "")
            files, quota = [], listing.get("quota")
            for f in listing.get("files", []):
                item = asr_export_one(f["filename"])
                item["event_ids"] = f.get("event_ids", [])
                quota = item.pop("quota", None) or quota
                files.append(item)
            emit({"date": args.date, "files": files, "quota": quota})


if __name__ == "__main__":
    main()
