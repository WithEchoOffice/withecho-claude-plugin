#!/usr/bin/env python3
"""读取 WithEcho 开放接口数据，输出 JSON 到 stdout。

  python fetch.py daily [--limit N] [--cursor C] [--all]
  python fetch.py daily-detail --event-id ID [ID ...]        # 多个 ID 一次拿齐
  python fetch.py digest (--date YYYY-MM-DD | --from YYYY-MM-DD --to YYYY-MM-DD)
  python fetch.py search --q 关键词 [--limit N]
  python fetch.py diary [--from YYYY-MM-DD --to YYYY-MM-DD | --limit N --cursor C]
  python fetch.py diary-detail --diary-id ID [ID ...]
  python fetch.py muse [--limit N] [--cursor C] [--all]
  python fetch.py tasks [--status S] [--limit N] [--cursor C] [--all]
  python fetch.py task-detail --task-id ID [ID ...]
  python fetch.py reminders [--status S] [--limit N] [--cursor C] [--all]
  python fetch.py asr-files (--date YYYY-MM-DD | --from YYYY-MM-DD --to YYYY-MM-DD)
  python fetch.py asr-export (--filename F1 [F2 ...] | --date YYYY-MM-DD)

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


class APIError(Exception):
    def __init__(self, error: str, description: str = ""):
        super().__init__(error)
        self.error, self.description = error, description


def request(path: str, params: dict, _retried=False) -> dict:
    """GET 开放接口；HTTP 错误抛 APIError（401 先自动刷新令牌重试一次）。"""
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
            return request(path, params, _retried=True)
        try:
            err = json.load(e)
        except json.JSONDecodeError:
            err = {"error": "http_%d" % e.code}
        raise APIError(err.get("error", "request_error"), err.get("error_description", ""))
    except urllib.error.URLError as e:
        raise APIError("network_error", str(e.reason))


def get(path: str, params: dict) -> dict:
    """request 的命令行包装：错误直接以 JSON 报错退出。"""
    try:
        return request(path, params)
    except APIError as e:
        raise auth.die(e.error, e.description)


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


DETAIL_BATCH = 50  # 服务端批量详情单次上限


def detail(path: str, id_key: str, ids: list) -> dict:
    """详情：单个 ID 走单条形态（响应即对象）；多个 ID 走批量形态 <id_key>s=a,b，
    按 50 个一批拿齐后合并成 {"<列表键>": [...]}（与入参同序，缺失项带 error=not_found）。"""
    ids = [i for i in dict.fromkeys(ids) if i]
    if len(ids) == 1:
        return get(path, {id_key: ids[0]})
    merged, list_key = [], None
    for i in range(0, len(ids), DETAIL_BATCH):
        resp = get(path, {id_key + "s": ",".join(ids[i:i + DETAIL_BATCH])})
        list_key = next(iter(resp), list_key)
        merged.extend(resp.get(list_key, []))
    return {list_key or "items": merged}


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


ASR_EXPORT_BATCH = 50  # 服务端单次导出上限


def asr_export_many(filenames: list) -> dict:
    """批量导出：本地命中的直接返回（不请求、不扣额度），其余按 50 个一批向服务器要并落盘。
    输出 files 与入参顺序一致，每项 source=local|server，或 error=not_found|quota_exceeded。"""
    seen, ordered = set(), []
    for f in filenames:
        if f and f not in seen:
            seen.add(f)
            ordered.append(f)
    results, missing = {}, []
    for f in ordered:
        cached = asr_cache_read(f)
        if cached is not None:
            results[f] = {"filename": f, "source": "local", "content": cached}
        else:
            missing.append(f)
    quota, exhausted = None, False
    for i in range(0, len(missing), ASR_EXPORT_BATCH):
        if exhausted:
            break  # 余量已尽，剩下的批次不再请求
        batch = missing[i:i + ASR_EXPORT_BATCH]
        try:
            resp = request("/open/asr/export", {"filename": ",".join(batch)})
        except APIError as e:
            if e.error == "quota_exceeded" and (results or i > 0):
                exhausted = True  # 已有本地/前几批结果，整体 429 只标剩余项，不丢已得内容
                continue
            raise auth.die(e.error, e.description)
        quota = resp.get("quota") or quota
        for item in resp.get("files", []):
            f = item.get("filename", "")
            if item.get("error"):
                results[f] = {"filename": f, "error": item["error"]}
            else:
                asr_cache_write(f, item.get("content", ""))
                results[f] = {"filename": f, "source": "server", "content": item.get("content", "")}
        if quota and quota.get("remaining") == 0:
            exhausted = True
    files = [results.get(f) or {"filename": f, "error": "quota_exceeded"} for f in ordered]
    return {"files": files, "quota": quota}


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

    dd = sub.add_parser("daily-detail", help="事件详情（正文+洞察卡片），可一次传多个 ID")
    dd.add_argument("--event-id", nargs="+", required=True)

    dg = sub.add_parser("digest", help="按天聚合：事件+日记正文+碎碎念（单日或最多 31 天区间）")
    dg.add_argument("--date", default="", metavar="YYYY-MM-DD")
    dg.add_argument("--from", dest="date_from", default="", metavar="YYYY-MM-DD")
    dg.add_argument("--to", dest="date_to", default="", metavar="YYYY-MM-DD")

    se = sub.add_parser("search", help="跨域检索（洞察卡片/日记/任务）")
    se.add_argument("--q", required=True, help="关键词，1-100 字符")
    se.add_argument("--limit", type=int, default=10, help="每段条数，最大 20")

    diary = sub.add_parser("diary", help="日记列表")
    diary.add_argument("--from", dest="date_from", default="", metavar="YYYY-MM-DD")
    diary.add_argument("--to", dest="date_to", default="", metavar="YYYY-MM-DD")
    diary.add_argument("--limit", type=int, default=20)
    diary.add_argument("--cursor", default="")

    dy = sub.add_parser("diary-detail", help="日记正文，可一次传多个 ID")
    dy.add_argument("--diary-id", nargs="+", required=True)

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

    tk = sub.add_parser("task-detail", help="任务详情（含 Markdown 交付物全文），可一次传多个 ID")
    tk.add_argument("--task-id", nargs="+", required=True)

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

    ae = sub.add_parser("asr-export", help="批量导出语音识别原文（本地缓存优先，未缓存的每个文件扣 1 次月配额）")
    ae.add_argument("--filename", nargs="*", default=[],
                    help="一个或多个 segments/YYYY/MM/DD/<id>.txt，来自 daily-detail 的 transcript_file 或 asr-files")
    ae.add_argument("--date", default="", metavar="YYYY-MM-DD", help="导出当天全部文件（已缓存的不再请求）")

    args = p.parse_args()

    if args.cmd == "daily":
        emit(paged("/open/daily", "events", args.limit, args.cursor, args.all))
    elif args.cmd == "daily-detail":
        emit(detail("/open/daily/detail", "event_id", args.event_id))
    elif args.cmd == "digest":
        if not args.date and not (args.date_from and args.date_to):
            raise auth.die("invalid_request", "需要 --date，或 --from 与 --to")
        emit(get("/open/digest", {"date": args.date, "from": args.date_from, "to": args.date_to}))
    elif args.cmd == "search":
        emit(get("/open/search", {"q": args.q, "limit": args.limit}))
    elif args.cmd == "diary":
        if args.date_from or args.date_to:
            emit(get("/open/diaries", {"from": args.date_from, "to": args.date_to}))
        else:
            emit(get("/open/diaries", {"limit": args.limit, "cursor": args.cursor}))
    elif args.cmd == "diary-detail":
        emit(detail("/open/diaries/detail", "diary_id", args.diary_id))
    elif args.cmd == "muse":
        emit(paged("/open/muses", "muses", args.limit, args.cursor, args.all))
    elif args.cmd == "tasks":
        emit(paged("/open/tasks", "tasks", args.limit, args.cursor, args.all,
                   extra={"status": args.status}))
    elif args.cmd == "task-detail":
        emit(detail("/open/tasks/detail", "task_id", args.task_id))
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
            emit(asr_export_many(args.filename))
        else:
            listing = asr_files(args.date, "", "")
            event_ids = {f["filename"]: f.get("event_ids", []) for f in listing.get("files", [])}
            out = asr_export_many(list(event_ids))
            for item in out["files"]:
                item["event_ids"] = event_ids.get(item["filename"], [])
            out["quota"] = out.get("quota") or listing.get("quota")
            emit({"date": args.date, **out})


if __name__ == "__main__":
    main()
