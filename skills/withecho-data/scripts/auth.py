#!/usr/bin/env python3
"""WithEcho OAuth 认证（公开应用：PKCE S256 + 本机回调，无 client_secret）。

命令：
  python auth.py login    # 打开浏览器完成授权，令牌存 ~/.withecho/credentials.json
  python auth.py refresh  # 手动刷新令牌（fetch.py 会自动调用，一般无需手动）
  python auth.py status   # 查看登录态
  python auth.py logout   # 吊销令牌并删除本地凭证

仅用 Python 3 标准库。错误以 JSON 输出到 stderr 并非 0 退出。
"""

import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

API_BASE = os.environ.get("WITHECHO_API_BASE", "https://api.withecho.cn")
CLIENT_ID = os.environ.get("WITHECHO_CLIENT_ID", "01KZ811QEVV9ZYBQM3NXYXQT8Z")
SCOPES = "profile daily:read diary:read muse:read task:read reminder:read"
CRED_PATH = os.path.expanduser("~/.withecho/credentials.json")
CRED_LOCK_PATH = CRED_PATH + ".lock"
LOGIN_TIMEOUT = 300  # 等待浏览器回调的秒数
REFRESH_AHEAD = 300  # access_token 剩余不足 5 分钟即提前刷新


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def die(error: str, description: str = "") -> "SystemExit":
    print(json.dumps({"error": error, "error_description": description},
                     ensure_ascii=False), file=sys.stderr)
    return SystemExit(1)


# ---------- 凭证存取 ----------

def load_credentials():
    try:
        with open(CRED_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_credentials(token: dict):
    """令牌落盘。refresh_token 轮换后旧值即废，必须原子写入（tmp + replace）。"""
    creds = {
        "access_token": token["access_token"],
        "refresh_token": token["refresh_token"],
        "expires_at": int(time.time()) + int(token.get("expires_in", 7200)),
        "scope": token.get("scope", ""),
        "openid": token.get("openid", ""),
    }
    os.makedirs(os.path.dirname(CRED_PATH), exist_ok=True)
    tmp = CRED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CRED_PATH)
    return creds


def delete_credentials():
    try:
        os.remove(CRED_PATH)
    except FileNotFoundError:
        pass


# ---------- 跨进程锁 ----------
# refresh 会轮换作废旧 refresh_token，并发刷新（同机多个 fetch.py 同时跑）
# 会拿同一个旧 rt 二次提交，触发服务端重用检测撤销全部令牌，必须串行。

try:
    import fcntl

    def _lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
except ImportError:  # Windows
    import msvcrt

    def _lock_file(f):
        while True:  # msvcrt 内部只重试 10 秒，包一层等到拿到为止
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.5)


@contextlib.contextmanager
def cred_lock():
    """锁随文件句柄关闭/进程退出自动释放，不会留死锁。"""
    os.makedirs(os.path.dirname(CRED_PATH), exist_ok=True)
    with open(CRED_LOCK_PATH, "a") as f:
        _lock_file(f)
        yield


# ---------- OAuth 请求 ----------

def token_endpoint(data: dict) -> dict:
    """POST /oauth/token。成功返回令牌 JSON；HTTP 4xx/5xx 返回 {"error": ...}；
    网络错误抛 urllib.error.URLError。"""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        API_BASE + "/oauth/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except json.JSONDecodeError:
            return {"error": "http_%d" % e.code}


def token_request(data: dict) -> dict:
    try:
        result = token_endpoint(data)
    except urllib.error.URLError as e:
        raise die("network_error", str(e.reason))
    if "error" in result:
        raise die(result["error"], result.get("error_description", ""))
    return result


def refresh(known: dict = None) -> dict:
    """刷新令牌（跨进程串行）。

    known 传调用方手里已失效的凭证：拿到锁后若磁盘上的 refresh_token 已与之
    不同，说明并发的另一个进程刚刷新过，直接复用其结果，不再发请求。
    invalid_grant（过期/已撤销）时清除本地凭证，引导重新 login。
    """
    with cred_lock():
        creds = load_credentials()
        if not creds:
            raise die("not_logged_in", "请先运行: python auth.py login")
        if known and creds["refresh_token"] != known["refresh_token"]:
            return creds

        result = None
        for attempt in (1, 2):
            try:
                result = token_endpoint({
                    "grant_type": "refresh_token",
                    "refresh_token": creds["refresh_token"],
                    "client_id": CLIENT_ID,
                })
            except urllib.error.URLError as e:
                result = {"error": "network_error", "error_description": str(e.reason)}
            err = result.get("error", "")
            # 网络错误/5xx 重试一次：请求可能已被服务端处理而响应丢失，
            # 立即重试仍可拿到新令牌
            if attempt == 1 and (err == "network_error" or err == "server_error"
                                 or err.startswith("http_5")):
                time.sleep(1)
                continue
            break

        if result.get("error") == "invalid_grant":
            delete_credentials()
            raise die("invalid_grant", "授权已过期或被撤销，请重新运行: python auth.py login")
        if "error" in result:
            raise die(result["error"], result.get("error_description", ""))
        return save_credentials(result)


def ensure_fresh() -> dict:
    """fetch.py 的入口：返回可用凭证，快过期时自动刷新。"""
    creds = load_credentials()
    if not creds:
        raise die("not_logged_in", "请先运行: python auth.py login")
    if creds["expires_at"] - time.time() < REFRESH_AHEAD:
        return refresh(known=creds)
    return creds


# ---------- login ----------

CALLBACK_HTML = """<!doctype html><meta charset="utf-8">
<title>WithEcho 授权完成</title>
<body style="font-family:system-ui;text-align:center;padding-top:15vh">
<h2>{msg}</h2><p>可以关闭此页面，回到终端继续。</p></body>"""


def login():
    if CLIENT_ID == "REPLACE_WITH_CLIENT_ID":
        raise die("missing_client_id",
                  "未配置 client_id：设置环境变量 WITHECHO_CLIENT_ID，或联系 WithEcho 获取")

    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    state = b64url(secrets.token_bytes(16))
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            q = urllib.parse.parse_qs(parsed.query)
            result["code"] = q.get("code", [""])[0]
            result["state"] = q.get("state", [""])[0]
            result["error"] = q.get("error", [""])[0]
            ok = result["code"] and not result["error"]
            msg = "授权成功" if ok else "授权未完成（%s）" % (result["error"] or "无回调参数")
            body = CALLBACK_HTML.format(msg=msg).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 静默默认访问日志
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    redirect_uri = "http://127.0.0.1:%d/callback" % port

    auth_url = API_BASE + "/oauth/authorize?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    print("已打开浏览器，请完成 WithEcho 授权。若未自动打开，请手动访问：\n\n  %s\n" % auth_url,
          file=sys.stderr)
    webbrowser.open(auth_url)

    server.timeout = 1
    deadline = time.time() + LOGIN_TIMEOUT
    try:
        while "code" not in result and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if "code" not in result:
        raise die("timeout", "等待授权回调超时（%d 秒），请重试" % LOGIN_TIMEOUT)
    if result["error"]:
        raise die(result["error"], "用户拒绝或授权失败")
    if result["state"] != state:
        raise die("state_mismatch", "state 校验失败，请重试")

    token = token_request({
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": redirect_uri,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    })
    creds = save_credentials(token)
    print(json.dumps({"logged_in": True, "openid": creds["openid"],
                      "scope": creds["scope"]}, ensure_ascii=False))


# ---------- status / logout ----------

def status():
    creds = load_credentials()
    if not creds:
        print(json.dumps({"logged_in": False}))
        return
    print(json.dumps({
        "logged_in": True,
        "openid": creds.get("openid", ""),
        "scope": creds.get("scope", ""),
        "access_token_expires_in": max(0, int(creds["expires_at"] - time.time())),
    }, ensure_ascii=False))


def logout():
    creds = load_credentials()
    if creds:
        # RFC 7009 不级联：refresh / access 各吊销一次，恒 200
        for token in (creds.get("refresh_token"), creds.get("access_token")):
            if not token:
                continue
            body = urllib.parse.urlencode({"token": token, "client_id": CLIENT_ID}).encode()
            req = urllib.request.Request(
                API_BASE + "/oauth/revoke", data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                urllib.request.urlopen(req, timeout=30).read()
            except (urllib.error.URLError, OSError):
                pass  # 吊销尽力而为，本地凭证一定删
    delete_credentials()
    print(json.dumps({"logged_in": False}))


def refresh_cmd():
    refresh()
    print(json.dumps({"refreshed": True}))


def main():
    commands = {"login": login, "refresh": refresh_cmd, "status": status, "logout": logout}
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in commands:
        print("用法: python auth.py {login|refresh|status|logout}", file=sys.stderr)
        sys.exit(2)
    commands[cmd]()


if __name__ == "__main__":
    main()
