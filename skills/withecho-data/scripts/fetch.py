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
  python fetch.py cache-clear                                # 清空当前账号的响应缓存（不动 ASR 原文）

所有读取命令都支持 --refresh：穿透本地缓存直接请求服务器，并用新结果覆盖缓存。

缓存（均按 openid 分目录，目录 0700 / 文件 0600）：
  ~/.withecho/cache/<openid>/   响应缓存。列表/搜索/聚合按「接口+参数」整条缓存，默认 10 分钟过期
                               （WITHECHO_CACHE_TTL 秒，0 = 不过期）；详情按单个 ID 缓存、永不过期
                               （多 ID 请求只向服务器要本地没有的那几个）。默认先读缓存，--refresh 穿透。
                               输出顶层带 "_cache": {"source": local|server|mixed, "fetched_at", "expires_at"?}
  ~/.withecho/asr/<openid>/     ASR 原文缓存。每个文件导出扣会员月配额，命中本地不请求、不扣额度；
                               --refresh 对 asr-export 不生效（重复导出照扣，没有意义）。

401 自动刷新令牌重试一次；仍失败则提示重新登录。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402

MAX_PAGES = 50  # --all 翻页保险上限
REFRESH = False  # --refresh：不读响应缓存，直接请求服务器并刷新缓存


def _env_ttl() -> int:
    raw = os.environ.get("WITHECHO_CACHE_TTL", "").strip()
    if not raw:
        return 600
    try:
        return max(0, int(raw))
    except ValueError:
        return 600


# 列表/搜索/聚合这类结果会随时间增长，命中本地超过 TTL 就自动穿透；0 = 不过期。
# 详情（按 ID）内容生成后不变，不受 TTL 限制。
CACHE_TTL = _env_ttl()


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
    """request 的命令行包装：错误直接以 JSON 报错退出（不走缓存）。"""
    try:
        return request(path, params)
    except APIError as e:
        raise auth.die(e.error, e.description)


def emit(data: dict):
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()


# ---------- 本地文件工具 ----------

OPENID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def current_openid() -> str:
    """缓存按账号分目录；openid 来自服务端，同样只认安全字符。"""
    creds = auth.load_credentials() or {}
    openid = creds.get("openid") or "default"
    return openid if OPENID_RE.match(openid) else "default"


def safe_join(root: str, *parts: str) -> str:
    """拼路径并保证结果在 root 之内（realpath 兜底，防 .. 穿越）。"""
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, *parts))
    if os.path.commonpath([root, path]) != root:
        raise auth.die("invalid_request", "非法路径：%r" % "/".join(parts))
    return path


def private_write(path: str, text: str):
    """私密内容落盘：目录 0700、文件 0600（创建即是，不依赖 umask），tmp + replace 原子写。"""
    auth.private_makedirs(os.path.dirname(path))
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o600)  # O_CREAT 的 mode 对已存在的 tmp 不生效，显式再收一次
    os.replace(tmp, path)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------- 响应缓存 ----------
# 默认读本地、没有才请求服务器；--refresh 穿透。整条缓存的查询（列表/搜索/聚合）带 TTL，
# 过期自动穿透；详情按 ID 永久缓存。调用方也可据 _cache.fetched_at 主动 --refresh（见 SKILL.md）。

CACHE_DIR = os.path.join(auth.BASE_DIR, "cache")


def cache_file(key: str) -> str:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return safe_join(CACHE_DIR, current_openid(), h[:2], h + ".json")


def cache_get(key: str, ttl: int = 0):
    """命中返回 {"fetched_at": ..., "ts": ..., "data": ...}，否则 None。
    ttl>0 时超过 ttl 秒视为过期（当未命中）；损坏的缓存也当未命中。"""
    if REFRESH:
        return None
    try:
        with open(cache_file(key), encoding="utf-8") as f:
            entry = json.load(f)
        if entry.get("key") != key or "data" not in entry:
            return None
        if ttl > 0 and time.time() - float(entry.get("ts", 0)) > ttl:
            return None  # 过期：自动穿透
        return entry
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None


def cache_put(key: str, data) -> str:
    fetched_at = now_iso()
    private_write(cache_file(key),
                  json.dumps({"key": key, "fetched_at": fetched_at, "ts": int(time.time()), "data": data},
                             ensure_ascii=False))
    return fetched_at


def iso_after(ts: float, seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts + seconds))


def query_key(path: str, params: dict) -> str:
    query = sorted((k, str(v)) for k, v in params.items() if v not in (None, ""))
    return path + "?" + urllib.parse.urlencode(query)


def cached_get(path: str, params: dict) -> dict:
    """按「接口+参数」整条缓存的 GET（带 TTL）。返回值顶层带 _cache 标明来源与过期时间。"""
    key = query_key(path, params)
    hit = cache_get(key, CACHE_TTL)
    if hit is not None:
        data = dict(hit["data"])
        data["_cache"] = {"source": "local", "fetched_at": hit["fetched_at"]}
        if CACHE_TTL > 0:
            data["_cache"]["expires_at"] = iso_after(float(hit.get("ts", 0)), CACHE_TTL)
        return data
    data = get(path, params)
    fetched_at = cache_put(key, data)
    data = dict(data)
    data["_cache"] = {"source": "server", "fetched_at": fetched_at}
    if CACHE_TTL > 0:
        data["_cache"]["expires_at"] = iso_after(time.time(), CACHE_TTL)
    return data


def merge_cache_meta(metas: list) -> dict:
    """多次请求合并输出时汇总 _cache：全 local / 全 server / mixed。"""
    sources = {m.get("source") for m in metas if m}
    if not sources:
        return {"source": "server", "fetched_at": now_iso()}
    source = sources.pop() if len(sources) == 1 else "mixed"
    meta = {"source": source,
            "fetched_at": min(m["fetched_at"] for m in metas if m and m.get("fetched_at"))}
    if source == "mixed":
        meta["local"] = sum(1 for m in metas if m and m.get("source") == "local")
        meta["server"] = sum(1 for m in metas if m and m.get("source") == "server")
    expires = [m["expires_at"] for m in metas if m and m.get("expires_at")]
    if expires:
        meta["expires_at"] = min(expires)
    return meta


def cache_clear() -> dict:
    """只清响应缓存；ASR 原文是花额度换来的，不动。"""
    target = safe_join(CACHE_DIR, current_openid())
    count = 0
    if os.path.isdir(target):
        for _, _, files in os.walk(target):
            count += sum(1 for f in files if f.endswith(".json"))
        shutil.rmtree(target)
    return {"cleared": count, "path": target}


# ---------- 读取 ----------

def paged(path: str, list_key: str, limit: int, cursor: str, fetch_all: bool,
          extra: dict = None) -> dict:
    """游标翻页；fetch_all 时聚合所有页到一个列表。每页各自缓存（键含 cursor）。"""
    extra = extra or {}
    if not fetch_all:
        return cached_get(path, {"limit": limit, "cursor": cursor, **extra})
    items, metas, pages = [], [], 0
    while pages < MAX_PAGES:
        resp = cached_get(path, {"limit": 50, "cursor": cursor, **extra})
        metas.append(resp.pop("_cache", None))
        items.extend(resp.get(list_key, []))
        cursor = resp.get("next_cursor", "")
        pages += 1
        if not cursor:
            break
    return {list_key: items, "next_cursor": cursor, "_cache": merge_cache_meta(metas)}


DETAIL_BATCH = 50  # 服务端批量详情单次上限


def detail(path: str, id_key: str, list_key: str, ids: list) -> dict:
    """详情按单个 ID 缓存：先查本地，缺的按 50 个一批向服务器批量要并逐条落盘。
    传 1 个 ID 时输出即该对象；多个时输出 {"<list_key>": [...]} 与入参同序，
    缺失项为 {"<id_key>": id, "error": "not_found"}（不缓存，数据可能尚未生成）。"""
    ids = [i for i in dict.fromkeys(ids) if i]
    found, metas, missing = {}, {}, []
    for i in ids:
        hit = cache_get(query_key(path, {id_key: i}))
        if hit is not None:
            found[i] = hit["data"]
            metas[i] = {"source": "local", "fetched_at": hit["fetched_at"]}
        else:
            missing.append(i)

    if len(ids) == 1 and missing:
        obj = get(path, {id_key: ids[0]})  # 单条形态：响应即对象；not_found 由 get 报错退出
        fetched_at = cache_put(query_key(path, {id_key: ids[0]}), obj)
        found[ids[0]] = obj
        metas[ids[0]] = {"source": "server", "fetched_at": fetched_at}
    else:
        for i in range(0, len(missing), DETAIL_BATCH):
            batch = missing[i:i + DETAIL_BATCH]
            resp = get(path, {id_key + "s": ",".join(batch)})
            for item in resp.get(list_key, []):
                iid = item.get(id_key)
                if iid not in batch:
                    continue  # 只认本批请求过的 ID
                found[iid] = item
                if item.get("error"):
                    metas[iid] = {"source": "server", "fetched_at": now_iso()}
                else:
                    metas[iid] = {"source": "server",
                                  "fetched_at": cache_put(query_key(path, {id_key: iid}), item)}

    if len(ids) == 1:
        out = dict(found[ids[0]])
        out["_cache"] = metas[ids[0]]
        return out
    items = [found.get(i) or {id_key: i, "error": "not_found"} for i in ids]
    return {list_key: items, "_cache": merge_cache_meta(list(metas.values()))}


# ---------- ASR 原文本地缓存 ----------
# 每导出一个转写文件都消耗会员月配额（同一文件重复导出照计），所以导出结果永久留在本地
# ~/.withecho/asr/<openid>/<filename>，之后先查本地、没有再向服务器要。按 openid 分目录，
# 换账号登录不会串数据，退出登录也不清（用户自己的数据，保留在本地）。

ASR_CACHE_DIR = os.path.join(auth.BASE_DIR, "asr")

# 文件名参与本地路径拼接，而它可能来自不可信来源（agent 参数、服务端响应），
# 必须白名单校验，杜绝 ".." 穿越读写缓存目录之外的文件。
ASR_FILENAME_RE = re.compile(r"^segments/\d{4}/\d{2}/\d{2}/[A-Za-z0-9_-]+\.txt$")


def valid_asr_filename(filename: str) -> bool:
    return bool(filename) and ASR_FILENAME_RE.match(filename) is not None


def asr_cache_path(filename: str) -> str:
    if not valid_asr_filename(filename):
        raise auth.die("invalid_request",
                       "非法转写文件名（须形如 segments/YYYY/MM/DD/<id>.txt）：%r" % filename)
    return safe_join(ASR_CACHE_DIR, current_openid(), *filename.split("/"))


def asr_cache_read(filename: str):
    try:
        with open(asr_cache_path(filename), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def asr_cache_write(filename: str, content: str):
    """原文是真实对话逐句转写，比令牌更敏感：目录 0700、文件 0600。"""
    private_write(asr_cache_path(filename), content)


ASR_EXPORT_BATCH = 50  # 服务端单次导出上限


def asr_export_many(filenames: list) -> dict:
    """批量导出：本地命中的直接返回（不请求、不扣额度），其余按 50 个一批向服务器要并落盘。
    输出 files 与入参顺序一致，每项 source=local|server，或 error=not_found|quota_exceeded。"""
    seen, ordered = set(), []
    for f in filenames:
        if f and f not in seen:
            seen.add(f)
            ordered.append(f)
    bad = [f for f in ordered if not valid_asr_filename(f)]
    if bad:
        raise auth.die("invalid_request",
                       "非法转写文件名（须形如 segments/YYYY/MM/DD/<id>.txt）：%s" % ", ".join(bad))
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
            if f not in batch:
                continue  # 只认本批请求过的文件名，服务端多回/乱回的不落盘
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
    resp = cached_get("/open/asr/files", params)
    for f in resp.get("files", []):
        name = f.get("filename", "")
        f["cached"] = valid_asr_filename(name) and os.path.exists(asr_cache_path(name))
    return resp


def main():
    global REFRESH
    p = argparse.ArgumentParser(description="读取 WithEcho 数据")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--refresh", action="store_true",
                        help="穿透本地缓存直接请求服务器，并用新结果覆盖缓存")

    daily = sub.add_parser("daily", parents=[common], help="日常事件列表")
    daily.add_argument("--limit", type=int, default=20)
    daily.add_argument("--cursor", default="")
    daily.add_argument("--all", action="store_true", help="自动翻页拉全部")

    dd = sub.add_parser("daily-detail", parents=[common], help="事件详情（正文+洞察卡片），可一次传多个 ID")
    dd.add_argument("--event-id", nargs="+", required=True)

    dg = sub.add_parser("digest", parents=[common], help="按天聚合：事件+日记正文+碎碎念（单日或最多 31 天区间）")
    dg.add_argument("--date", default="", metavar="YYYY-MM-DD")
    dg.add_argument("--from", dest="date_from", default="", metavar="YYYY-MM-DD")
    dg.add_argument("--to", dest="date_to", default="", metavar="YYYY-MM-DD")

    se = sub.add_parser("search", parents=[common], help="跨域检索（洞察卡片/日记/任务）")
    se.add_argument("--q", required=True, help="关键词，1-100 字符")
    se.add_argument("--limit", type=int, default=10, help="每段条数，最大 20")

    diary = sub.add_parser("diary", parents=[common], help="日记列表")
    diary.add_argument("--from", dest="date_from", default="", metavar="YYYY-MM-DD")
    diary.add_argument("--to", dest="date_to", default="", metavar="YYYY-MM-DD")
    diary.add_argument("--limit", type=int, default=20)
    diary.add_argument("--cursor", default="")

    dy = sub.add_parser("diary-detail", parents=[common], help="日记正文，可一次传多个 ID")
    dy.add_argument("--diary-id", nargs="+", required=True)

    muse = sub.add_parser("muse", parents=[common], help="碎碎念列表（自带正文）")
    muse.add_argument("--limit", type=int, default=20)
    muse.add_argument("--cursor", default="")
    muse.add_argument("--all", action="store_true", help="自动翻页拉全部")

    tasks = sub.add_parser("tasks", parents=[common], help="研究任务列表")
    tasks.add_argument("--status", default="",
                       help="proposed/confirmed/running/completed/failed/cancelled，空为全部")
    tasks.add_argument("--limit", type=int, default=20)
    tasks.add_argument("--cursor", default="")
    tasks.add_argument("--all", action="store_true", help="自动翻页拉全部")

    tk = sub.add_parser("task-detail", parents=[common], help="任务详情（含 Markdown 交付物全文），可一次传多个 ID")
    tk.add_argument("--task-id", nargs="+", required=True)

    rem = sub.add_parser("reminders", parents=[common], help="提醒列表（默认只看生效中的）")
    rem.add_argument("--status", default="",
                     help="默认 active；可传 proposed/triggered/cancelled/expired 或 all")
    rem.add_argument("--limit", type=int, default=20)
    rem.add_argument("--cursor", default="")
    rem.add_argument("--all", action="store_true", help="自动翻页拉全部")

    af = sub.add_parser("asr-files", parents=[common], help="按天列出语音识别原文文件与事件对应关系（不扣额度）")
    af.add_argument("--date", default="", metavar="YYYY-MM-DD")
    af.add_argument("--from", dest="date_from", default="", metavar="YYYY-MM-DD")
    af.add_argument("--to", dest="date_to", default="", metavar="YYYY-MM-DD")

    ae = sub.add_parser("asr-export", parents=[common],
                        help="批量导出语音识别原文（本地缓存优先，未缓存的每个文件扣 1 次月配额）")
    ae.add_argument("--filename", nargs="*", default=[],
                    help="一个或多个 segments/YYYY/MM/DD/<id>.txt，来自 daily-detail 的 transcript_file 或 asr-files")
    ae.add_argument("--date", default="", metavar="YYYY-MM-DD", help="导出当天全部文件（已缓存的不再请求）")

    sub.add_parser("cache-clear", help="清空当前账号的响应缓存（ASR 原文缓存不动）")

    args = p.parse_args()
    REFRESH = bool(getattr(args, "refresh", False))

    if args.cmd == "daily":
        emit(paged("/open/daily", "events", args.limit, args.cursor, args.all))
    elif args.cmd == "daily-detail":
        emit(detail("/open/daily/detail", "event_id", "events", args.event_id))
    elif args.cmd == "digest":
        if not args.date and not (args.date_from and args.date_to):
            raise auth.die("invalid_request", "需要 --date，或 --from 与 --to")
        emit(cached_get("/open/digest", {"date": args.date, "from": args.date_from, "to": args.date_to}))
    elif args.cmd == "search":
        emit(cached_get("/open/search", {"q": args.q, "limit": args.limit}))
    elif args.cmd == "diary":
        if args.date_from or args.date_to:
            emit(cached_get("/open/diaries", {"from": args.date_from, "to": args.date_to}))
        else:
            emit(cached_get("/open/diaries", {"limit": args.limit, "cursor": args.cursor}))
    elif args.cmd == "diary-detail":
        emit(detail("/open/diaries/detail", "diary_id", "diaries", args.diary_id))
    elif args.cmd == "muse":
        emit(paged("/open/muses", "muses", args.limit, args.cursor, args.all))
    elif args.cmd == "tasks":
        emit(paged("/open/tasks", "tasks", args.limit, args.cursor, args.all,
                   extra={"status": args.status}))
    elif args.cmd == "task-detail":
        emit(detail("/open/tasks/detail", "task_id", "tasks", args.task_id))
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
            event_ids = {f["filename"]: f.get("event_ids", []) for f in listing.get("files", [])
                         if valid_asr_filename(f.get("filename", ""))}
            out = asr_export_many(list(event_ids))
            for item in out["files"]:
                item["event_ids"] = event_ids.get(item["filename"], [])
            out["quota"] = out.get("quota") or listing.get("quota")
            emit({"date": args.date, **out})
    elif args.cmd == "cache-clear":
        emit(cache_clear())


if __name__ == "__main__":
    main()
