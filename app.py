#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import base64
import copy
import hashlib
import hmac
import html
import json
import os
import random
import secrets
import sqlite3
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "tieba.db"
SECRET_PATH = BASE_DIR / ".app_secret"
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
SIGN_DELAY_MIN = float(os.getenv("SIGN_DELAY_MIN", "0.2"))
SIGN_DELAY_MAX = float(os.getenv("SIGN_DELAY_MAX", "0.8"))
APP_VERSION = "v1.0.1"
if SIGN_DELAY_MAX < SIGN_DELAY_MIN:
    SIGN_DELAY_MIN, SIGN_DELAY_MAX = SIGN_DELAY_MAX, SIGN_DELAY_MIN

LIKE_URL = "http://c.tieba.baidu.com/c/f/forum/like"
TBS_URL = "http://tieba.baidu.com/dc/common/tbs"
SIGN_URL = "http://c.tieba.baidu.com/c/c/forum/sign"
SIGN_KEY = "tiebaclient!!!"

HEADERS = {
    "Host": "tieba.baidu.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
}
SIGN_DATA = {
    "_client_type": "2",
    "_client_version": "9.7.8.0",
    "_phone_imei": "000000000000000",
    "model": "MI+5",
    "net_type": "1",
}

DB_LOCK = threading.RLock()
HTTP = requests.Session()


def load_secret():
    env_secret = os.getenv("APP_SECRET")
    if env_secret:
        return hashlib.sha256(env_secret.encode()).digest()
    if SECRET_PATH.exists():
        return base64.urlsafe_b64decode(SECRET_PATH.read_text().strip())
    secret = secrets.token_bytes(32)
    SECRET_PATH.write_text(base64.urlsafe_b64encode(secret).decode())
    return secret


APP_SECRET = load_secret()
AES = AESGCM(APP_SECRET)


def now():
    return int(time.time())


def today_key():
    return time.strftime("%Y-%m-%d", time.localtime())


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with DB_LOCK, db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                bduss_cipher TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                last_sync_at INTEGER,
                last_sign_at INTEGER,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS forums (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                forum_id TEXT NOT NULL,
                name TEXT NOT NULL,
                is_followed INTEGER NOT NULL DEFAULT 1,
                is_confirmed INTEGER NOT NULL DEFAULT 0,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL,
                UNIQUE(account_id, forum_id)
            );
            CREATE TABLE IF NOT EXISTS sign_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                forum_id TEXT NOT NULL,
                forum_name TEXT NOT NULL,
                sign_date TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                raw_response TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(account_id, forum_id, sign_date)
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_records_date ON sign_records(sign_date, status);
            CREATE INDEX IF NOT EXISTS idx_forums_account ON forums(account_id, is_confirmed, is_enabled);
            """
        )


def query(sql, params=()):
    with DB_LOCK, db() as conn:
        return conn.execute(sql, params).fetchall()


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with DB_LOCK, db() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def audit(user_id, action, detail):
    execute("INSERT INTO audit_logs(user_id, action, detail, created_at) VALUES(?,?,?,?)", (user_id, action, detail, now()))


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240000).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password, stored):
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240000).hex()
    return hmac.compare_digest(candidate, digest)


def encrypt_text(value):
    nonce = secrets.token_bytes(12)
    encrypted = AES.encrypt(nonce, value.encode(), None)
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def decrypt_text(value):
    raw = base64.urlsafe_b64decode(value.encode())
    return AES.decrypt(raw[:12], raw[12:], None).decode()


def encode_data(data):
    sign_src = "".join(f"{key}={data[key]}" for key in sorted(data.keys()))
    data["sign"] = hashlib.md5((sign_src + SIGN_KEY).encode("utf-8")).hexdigest().upper()
    return data


def get_tbs(bduss):
    headers = copy.copy(HEADERS)
    headers["Cookie"] = f"BDUSS={bduss}"
    res = HTTP.get(TBS_URL, headers=headers, timeout=8)
    res.raise_for_status()
    data = res.json()
    if not data.get("is_login") and not data.get("tbs"):
        raise RuntimeError("BDUSS 可能失效")
    return data["tbs"]


def flatten_forums(items):
    result = []
    for item in items or []:
        if isinstance(item, list):
            result.extend(flatten_forums(item))
        elif isinstance(item, dict):
            forum_id = item.get("id") or item.get("fid")
            name = item.get("name") or item.get("forum_name")
            if forum_id and name:
                result.append({"id": str(forum_id), "name": str(name)})
    return result


def get_favorites(bduss):
    collected = []
    page = 1
    while True:
        payload = encode_data({
            "BDUSS": bduss,
            "_client_type": "2",
            "_client_id": "wappc_1534235498291_488",
            "_client_version": "9.7.8.0",
            "_phone_imei": "000000000000000",
            "from": "1008621y",
            "page_no": str(page),
            "page_size": "200",
            "model": "MI+5",
            "net_type": "1",
            "timestamp": str(now()),
            "vcode_tag": "11",
        })
        res = HTTP.post(LIKE_URL, data=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        forum_list = data.get("forum_list") or {}
        collected.extend(flatten_forums(forum_list.get("non-gconforum", [])))
        collected.extend(flatten_forums(forum_list.get("gconforum", [])))
        if data.get("has_more") != "1":
            break
        page += 1
        time.sleep(0.4)
    unique = {}
    for item in collected:
        unique[item["id"]] = item
    return list(unique.values())


def client_sign(bduss, tbs, forum_id, name):
    payload = copy.copy(SIGN_DATA)
    payload.update({"BDUSS": bduss, "fid": forum_id, "kw": name, "tbs": tbs, "timestamp": str(now())})
    res = HTTP.post(SIGN_URL, data=encode_data(payload), timeout=10)
    res.raise_for_status()
    return res.json()


def sync_account(account, auto_confirm_new=True):
    bduss = decrypt_text(account["bduss_cipher"])
    forums = get_favorites(bduss)
    seen_ids = {item["id"] for item in forums}
    for item in forums:
        execute(
            """
            INSERT INTO forums(account_id, forum_id, name, is_followed, is_confirmed, is_enabled, updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(account_id, forum_id) DO UPDATE SET
                name=excluded.name,
                is_followed=1,
                updated_at=excluded.updated_at
            """,
            (account["id"], item["id"], item["name"], 1, 1 if auto_confirm_new else 0, 1, now()),
        )
    existing = query("SELECT forum_id FROM forums WHERE account_id=?", (account["id"],))
    for row in existing:
        if row["forum_id"] not in seen_ids:
            execute("UPDATE forums SET is_followed=0, is_enabled=0, updated_at=? WHERE account_id=? AND forum_id=?", (now(), account["id"], row["forum_id"]))
    execute("UPDATE accounts SET last_sync_at=? WHERE id=?", (now(), account["id"]))
    audit(account["user_id"], "sync_forums", f"账号 {account['label']} 同步 {len(forums)} 个关注贴吧")
    return forums


def sign_account(account, manual=False):
    if account["status"] != "active":
        return []
    bduss = decrypt_text(account["bduss_cipher"])
    forums = query(
        "SELECT * FROM forums WHERE account_id=? AND is_followed=1 AND is_confirmed=1 AND is_enabled=1 ORDER BY name",
        (account["id"],),
    )
    if not forums:
        audit(account["user_id"], "sign_skipped", f"账号 {account['label']} 没有已确认贴吧")
        return []
    tbs = get_tbs(bduss)
    results = []
    for forum in forums:
        existing = query_one(
            "SELECT id, status FROM sign_records WHERE account_id=? AND forum_id=? AND sign_date=?",
            (account["id"], forum["forum_id"], today_key()),
        )
        if existing and existing["status"] == "success":
            continue
        if SIGN_DELAY_MAX > 0:
            time.sleep(random.uniform(SIGN_DELAY_MIN, SIGN_DELAY_MAX))
        status = "failed"
        message = "签到失败"
        raw = None
        try:
            raw_data = client_sign(bduss, tbs, forum["forum_id"], forum["name"])
            raw = json.dumps(raw_data, ensure_ascii=False)[:4000]
            error_code = str(raw_data.get("error_code", raw_data.get("no", "")))
            error_msg = raw_data.get("error_msg") or raw_data.get("error") or ""
            if error_code in ("0", "160002") or "已签到" in error_msg:
                status = "success"
                message = "签到成功" if error_code != "160002" else "今天已经签过"
            elif "vcode" in raw.lower() or "验证码" in error_msg or "风控" in error_msg:
                status = "paused_need_user"
                message = "需要人工处理验证码或风控"
                execute("UPDATE accounts SET status='paused' WHERE id=?", (account["id"],))
            else:
                message = error_msg or f"返回码 {error_code or '未知'}"
        except Exception as exc:
            message = str(exc)[:500]
        execute(
            """
            INSERT INTO sign_records(user_id, account_id, forum_id, forum_name, sign_date, status, message, raw_response, created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account_id, forum_id, sign_date) DO UPDATE SET
                status=excluded.status,
                message=excluded.message,
                raw_response=excluded.raw_response,
                created_at=excluded.created_at
            """,
            (account["user_id"], account["id"], forum["forum_id"], forum["name"], today_key(), status, message, raw, now()),
        )
        results.append({"forum": forum["name"], "status": status, "message": message})
    execute("UPDATE accounts SET last_sign_at=? WHERE id=?", (now(), account["id"]))
    audit(account["user_id"], "manual_sign" if manual else "scheduled_sign", f"账号 {account['label']} 签到 {len(results)} 个贴吧")
    return results


def scheduler_loop():
    while True:
        try:
            hour = int(time.strftime("%H", time.localtime()))
            if 6 <= hour <= 23:
                accounts = query("SELECT * FROM accounts WHERE status='active' ORDER BY id")
                for account in accounts:
                    signed_today = query_one(
                        "SELECT 1 FROM sign_records WHERE account_id=? AND sign_date=? AND status IN ('success','paused_need_user') LIMIT 1",
                        (account["id"], today_key()),
                    )
                    if not signed_today:
                        sync_account(account)
                        sign_account(account, manual=False)
                        time.sleep(random.uniform(3.0, 8.0))
        except Exception as exc:
            audit(None, "scheduler_error", str(exc)[:500])
        time.sleep(300)


def esc(value):
    return html.escape(str(value or ""), quote=True)


def ts(value):
    if not value:
        return "从未"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(value)))


def page(title, body, user=None):
    admin_link = ""
    if user and user["role"] == "admin":
        admin_link = '<a class="focusable rounded-xl px-3 py-2 hover:bg-[#dff7ef]" href="/admin">管理员后台</a>'
    auth = ""
    if user:
        auth = f'<span class="text-sm text-[color:var(--muted)]">{esc(user["username"])}</span><a class="focusable rounded-xl px-3 py-2 hover:bg-[#dff7ef]" href="/password">修改密码</a><a class="focusable rounded-xl px-3 py-2 hover:bg-[#dff7ef]" href="/logout">退出</a>'
    else:
        auth = '<a class="focusable rounded-xl px-3 py-2 hover:bg-[#dff7ef]" href="/login">登录</a><a class="focusable rounded-xl bg-[color:var(--orange)] px-3 py-2 font-bold text-[#2a1207]" href="/register">注册</a>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(title)}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://code.iconify.design/iconify-icon/2.1.0/iconify-icon.min.js"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&family=Noto+Sans+SC:wght@400;500;700;800&display=swap');
    :root {{--mint:#078a78;--orange:#f07a2b;--paper:#12312c;--muted:#5d766f;--line:rgba(18,49,44,.14);--panel:rgba(255,255,255,.82);}}
    body {{font-family:'Plus Jakarta Sans','Noto Sans SC',system-ui,sans-serif;color:var(--paper);background:radial-gradient(circle at 6% 10%,rgba(73,222,190,.28),transparent 27rem),radial-gradient(circle at 88% 12%,rgba(255,170,92,.24),transparent 24rem),linear-gradient(135deg,#f7fffb,#eef8f4 48%,#fff4e8);min-height:100vh;}}
    body:before {{content:'';position:fixed;inset:0;pointer-events:none;opacity:.32;background-image:linear-gradient(rgba(18,49,44,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(18,49,44,.05) 1px,transparent 1px);background-size:42px 42px;}}
    .panel {{border:1px solid var(--line);background:var(--panel);box-shadow:0 28px 80px rgba(71,99,91,.16),inset 0 1px 0 rgba(255,255,255,.72);backdrop-filter:blur(20px);}}
    .focusable:focus-visible {{outline:2px solid var(--orange);outline-offset:4px;}}
    input,textarea,select {{background:rgba(255,255,255,.72);border:1px solid var(--line);color:var(--paper);}}
    input::placeholder,textarea::placeholder {{color:rgba(93,118,111,.72);}}
    @media (prefers-reduced-motion: reduce) {{* {{scroll-behavior:auto!important;transition:none!important;animation:none!important;}}}}
  </style>
</head>
<body>
  <header class="relative z-10 px-5 pt-5 md:px-8">
    <nav class="panel mx-auto flex max-w-7xl items-center justify-between rounded-[2rem] px-4 py-3">
      <a class="focusable flex items-center gap-3 rounded-xl" href="/">
        <span class="grid h-10 w-10 place-items-center rounded-2xl" style="background:linear-gradient(135deg,#30d9bd,#078a78);color:#f7fffb"><iconify-icon icon="ph:seal-check-bold" width="23" height="23"></iconify-icon></span>
        <span class="font-extrabold">贴吧自动签到</span>
      </a>
      <div class="flex items-center gap-2 text-sm">{admin_link}{auth}</div>
    </nav>
  </header>
  <main class="relative z-10 mx-auto max-w-7xl px-5 py-8 md:px-8">{body}</main>
</body>
</html>"""


def card(content, extra=""):
    return f'<section class="panel rounded-[2rem] p-5 md:p-6 {extra}">{content}</section>'


def form_page(title, action, fields, submit, error=""):
    error_html = f'<p class="mb-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700 ring-1 ring-red-200">{esc(error)}</p>' if error else ""
    fields_html = "".join(fields)
    return card(f"""
      <p class="text-sm uppercase tracking-[.24em] text-[color:var(--mint)]">Account</p>
      <h1 class="mt-3 text-4xl font-extrabold tracking-[-.06em]">{esc(title)}</h1>
      {error_html}
      <form class="mt-6 grid gap-4" method="post" action="{action}">
        {fields_html}
        <button class="focusable h-12 cursor-pointer rounded-2xl bg-[color:var(--orange)] font-extrabold text-[#2a1207] hover:bg-[#ffad73]" type="submit">{esc(submit)}</button>
      </form>
    """, "max-w-xl")


def input_field(name, label, type_="text", placeholder="", required=True):
    req = "required" if required else ""
    return f'<label class="grid gap-2 text-sm font-bold" for="{name}">{esc(label)}<input class="focusable h-12 rounded-2xl px-4" id="{name}" name="{name}" type="{type_}" placeholder="{esc(placeholder)}" {req}></label>'


class App(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_html(self, html_text, status=200):
        data = html_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, path):
        self.send_response(302)
        self.send_header("Location", path)
        self.end_headers()

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def post_data(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return {k: v[0] if v else "" for k, v in urllib.parse.parse_qs(raw).items()}

    def current_user(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie.get("session")
        if not token:
            return None
        row = query_one(
            "SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires_at>?",
            (token.value, now()),
        )
        if row and row["status"] == "active":
            return row
        return None

    def require_user(self):
        user = self.current_user()
        if not user:
            self.redirect("/login")
            return None
        return user

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        user = self.current_user()
        if path == "/":
            if user:
                self.redirect("/dashboard")
                return
            self.send_html(page("贴吧自动签到", landing(), user))
        elif path == "/login":
            self.send_html(page("登录", form_page("回来就好", "/login", [input_field("username", "用户名"), input_field("password", "密码", "password")], "登录"), user))
        elif path == "/register":
            self.send_html(page("注册", form_page("先建个账号", "/register", [input_field("username", "用户名"), input_field("password", "密码", "password")], "注册"), user))
        elif path == "/password":
            user = self.require_user()
            if user:
                self.send_html(page("修改密码", form_page("修改密码", "/password", [input_field("old_password", "当前密码", "password"), input_field("new_password", "新密码", "password"), input_field("confirm_password", "确认新密码", "password")], "保存新密码"), user))
        elif path == "/logout":
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie.get("session")
            if token:
                execute("DELETE FROM sessions WHERE token=?", (token.value,))
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", "session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
            self.end_headers()
        elif path == "/dashboard":
            user = self.require_user()
            if user:
                self.send_html(page("控制台", dashboard(user), user))
        elif path.startswith("/account/") and path.endswith("/forums"):
            user = self.require_user()
            if user:
                account_id = int(path.split("/")[2])
                self.send_html(page("确认贴吧", forums_page(user, account_id), user))
        elif path == "/admin":
            user = self.require_user()
            if user:
                if user["role"] != "admin":
                    self.send_html(page("无权限", card("<h1 class='text-3xl font-extrabold'>你不是管理员。</h1>"), user), HTTPStatus.FORBIDDEN)
                else:
                    self.send_html(page("管理员后台", admin_page(), user))
        else:
            self.send_html(page("404", card("<h1 class='text-3xl font-extrabold'>这页走丢了。</h1>"), user), HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        data = self.post_data()
        user = self.current_user()
        try:
            if path == "/register":
                self.register(data)
            elif path == "/login":
                self.login(data)
            elif path == "/password":
                user = self.require_user()
                if user:
                    self.change_password(user, data)
                    self.redirect("/dashboard")
            elif path == "/account/add":
                user = self.require_user()
                if user:
                    label = data.get("label", "").strip() or "百度账号"
                    bduss = data.get("bduss", "").strip()
                    if len(bduss) < 20:
                        raise RuntimeError("BDUSS 看起来太短")
                    account_id = execute("INSERT INTO accounts(user_id, label, bduss_cipher, created_at) VALUES(?,?,?,?)", (user["id"], label, encrypt_text(bduss), now()))
                    audit(user["id"], "add_account", f"添加账号 {label}")
                    account = query_one("SELECT * FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"]))
                    sync_account(account)
                    self.redirect(f"/account/{account_id}/forums")
            elif path.startswith("/account/") and path.endswith("/sync"):
                user = self.require_user()
                if user:
                    account = self.owned_account(user, int(path.split("/")[2]))
                    sync_account(account)
                    self.redirect(f"/account/{account['id']}/forums")
            elif path.startswith("/account/") and path.endswith("/confirm"):
                user = self.require_user()
                if user:
                    account = self.owned_account(user, int(path.split("/")[2]))
                    selected = set(data.get("forums", "").split(",")) if data.get("forums") else set()
                    all_forums = query("SELECT * FROM forums WHERE account_id=? AND is_followed=1", (account["id"],))
                    for forum in all_forums:
                        enabled = 1 if forum["forum_id"] in selected else 0
                        execute("UPDATE forums SET is_confirmed=1, is_enabled=?, updated_at=? WHERE id=?", (enabled, now(), forum["id"]))
                    audit(user["id"], "confirm_forums", f"账号 {account['label']} 确认 {len(selected)} 个贴吧")
                    self.redirect("/dashboard")
            elif path.startswith("/account/") and path.endswith("/sign"):
                user = self.require_user()
                if user:
                    account = self.owned_account(user, int(path.split("/")[2]))
                    sign_account(account, manual=True)
                    self.redirect("/dashboard")
            elif path.startswith("/account/") and path.endswith("/delete"):
                user = self.require_user()
                if user:
                    account = self.owned_account(user, int(path.split("/")[2]))
                    execute("DELETE FROM accounts WHERE id=? AND user_id=?", (account["id"], user["id"]))
                    audit(user["id"], "delete_account", f"删除账号 {account['label']}")
                    self.redirect("/dashboard")
            elif path.startswith("/admin/user/") and path.endswith("/toggle"):
                user = self.require_user()
                if user and user["role"] == "admin":
                    target_id = int(path.split("/")[3])
                    if target_id == user["id"]:
                        raise RuntimeError("不能封禁当前登录的管理员")
                    target = query_one("SELECT * FROM users WHERE id=?", (target_id,))
                    if not target:
                        raise RuntimeError("用户不存在")
                    new_status = "active" if target["status"] != "active" else "banned"
                    execute("UPDATE users SET status=? WHERE id=?", (new_status, target_id))
                    if new_status != "active":
                        execute("DELETE FROM sessions WHERE user_id=?", (target_id,))
                    audit(user["id"], "admin_toggle_user", f"用户 {target['username']} -> {new_status}")
                    self.redirect("/admin")
            elif path.startswith("/admin/user/") and path.endswith("/password"):
                user = self.require_user()
                if user and user["role"] == "admin":
                    target_id = int(path.split("/")[3])
                    target = query_one("SELECT * FROM users WHERE id=?", (target_id,))
                    if not target:
                        raise RuntimeError("用户不存在")
                    if target["role"] != "user":
                        raise RuntimeError("只能修改普通用户密码")
                    new_password = data.get("new_password", "")
                    if len(new_password) < 6:
                        raise RuntimeError("新密码至少 6 位")
                    execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), target_id))
                    execute("DELETE FROM sessions WHERE user_id=?", (target_id,))
                    audit(user["id"], "admin_change_password", f"修改用户 {target['username']} 的密码")
                    self.redirect("/admin")
            elif path.startswith("/admin/user/") and path.endswith("/delete"):
                user = self.require_user()
                if user and user["role"] == "admin":
                    target_id = int(path.split("/")[3])
                    if target_id == user["id"]:
                        raise RuntimeError("不能删除当前登录的管理员")
                    target = query_one("SELECT * FROM users WHERE id=?", (target_id,))
                    if not target:
                        raise RuntimeError("用户不存在")
                    execute("DELETE FROM users WHERE id=?", (target_id,))
                    audit(user["id"], "admin_delete_user", f"删除用户 {target['username']}")
                    self.redirect("/admin")
            elif path.startswith("/admin/account/") and path.endswith("/toggle"):
                user = self.require_user()
                if user and user["role"] == "admin":
                    account_id = int(path.split("/")[3])
                    account = query_one("SELECT * FROM accounts WHERE id=?", (account_id,))
                    new_status = "active" if account["status"] != "active" else "paused"
                    execute("UPDATE accounts SET status=? WHERE id=?", (new_status, account_id))
                    audit(user["id"], "admin_toggle_account", f"账号 {account_id} -> {new_status}")
                    self.redirect("/admin")
            else:
                self.send_html(page("404", card("<h1 class='text-3xl font-extrabold'>接口不存在。</h1>"), user), HTTPStatus.NOT_FOUND)
        except Exception as exc:
            content = card(f"<h1 class='text-3xl font-extrabold'>出错了。</h1><p class='mt-4 text-[color:var(--muted)]'>{esc(exc)}</p><a class='mt-6 inline-block rounded-2xl bg-[color:var(--orange)] px-5 py-3 font-bold text-[#2a1207]' href='/dashboard'>回控制台</a>")
            self.send_html(page("错误", content, user), HTTPStatus.BAD_REQUEST)

    def owned_account(self, user, account_id):
        account = query_one("SELECT * FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"]))
        if not account:
            raise RuntimeError("账号不存在")
        return account

    def register(self, data):
        username = data.get("username", "").strip()
        password = data.get("password", "")
        if len(username) < 3 or len(password) < 6:
            raise RuntimeError("用户名至少 3 位，密码至少 6 位")
        has_user = query_one("SELECT id FROM users LIMIT 1")
        role = "admin" if not has_user else "user"
        user_id = execute("INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,?,?)", (username, hash_password(password), role, now()))
        audit(user_id, "register", f"注册为 {role}")
        self.create_session(user_id)

    def login(self, data):
        user = query_one("SELECT * FROM users WHERE username=?", (data.get("username", "").strip(),))
        if not user or not verify_password(data.get("password", ""), user["password_hash"]):
            raise RuntimeError("用户名或密码不对")
        if user["status"] != "active":
            raise RuntimeError("账号已暂停")
        audit(user["id"], "login", "用户登录")
        self.create_session(user["id"])

    def change_password(self, user, data):
        old_password = data.get("old_password", "")
        new_password = data.get("new_password", "")
        confirm_password = data.get("confirm_password", "")
        if not verify_password(old_password, user["password_hash"]):
            raise RuntimeError("当前密码不对")
        if len(new_password) < 6:
            raise RuntimeError("新密码至少 6 位")
        if new_password != confirm_password:
            raise RuntimeError("两次输入的新密码不一致")
        execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user["id"]))
        audit(user["id"], "change_password", "修改登录密码")

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        execute("INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES(?,?,?,?)", (token, user_id, now(), now() + 86400 * 14))
        self.send_response(302)
        self.send_header("Location", "/dashboard")
        self.send_header("Set-Cookie", f"session={token}; Max-Age={86400 * 14}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()


def landing():
    return f"""
    <section class="grid items-start gap-7 py-6 lg:grid-cols-[1.12fr_.88fr] lg:py-12">
      <div class="pt-4 lg:pt-10">
        <div class="mb-6 inline-flex rounded-full border border-[color:var(--line)] bg-[#eefbf6] px-4 py-2 text-sm font-bold text-[color:var(--mint)]">服务器签到，先确认再执行</div>
        <h1 class="max-w-4xl text-[3.5rem] font-extrabold leading-[.9] tracking-[-.08em] md:text-[7.4rem]">贴吧签到，<br>不再麻烦。</h1>
        <p class="mt-7 max-w-2xl text-lg leading-8 text-[color:var(--muted)]">填入 BDUSS，系统同步关注贴吧。你勾选名单后，服务器每天自动处理签到。</p>
        <div class="mt-8 flex flex-wrap gap-3"><a class="focusable rounded-2xl bg-[color:var(--orange)] px-6 py-4 font-extrabold text-[#2a1207] shadow-lg shadow-orange-200/60" href="/login">开始使用</a><a class="focusable rounded-2xl border border-[color:var(--line)] bg-white/60 px-6 py-4 font-bold" href="/register">注册账号</a></div>
        <div class="mt-8 flex flex-wrap gap-3 text-sm text-[color:var(--muted)]"><span class="rounded-full bg-white/70 px-4 py-2">BDUSS 加密保存</span><span class="rounded-full bg-white/70 px-4 py-2">结果自动通知</span><span class="rounded-full bg-white/70 px-4 py-2">24小时运行</span></div>
      </div>
      {card('''
        <p class="text-sm uppercase tracking-[.24em] text-[color:var(--mint)]">Flow</p>
        <h2 class="mt-3 text-3xl font-extrabold tracking-[-.04em]">三步就够。</h2>
        <div class="mt-6 grid gap-3">
          <div class="rounded-[1.4rem] bg-[#f4fbf7] p-4"><p class="text-sm font-extrabold">01 添加账号</p><p class="mt-1 text-sm text-[color:var(--muted)]">只填写 BDUSS，不保存百度密码。</p></div>
          <div class="ml-5 rounded-[1.4rem] bg-[#fff7ef] p-4"><p class="text-sm font-extrabold">02 确认贴吧</p><p class="mt-1 text-sm text-[color:var(--muted)]">系统拉取关注列表，你决定签哪些。</p></div>
          <div class="rounded-[1.4rem] bg-[#eefbf6] p-4"><p class="text-sm font-extrabold">03 自动签到</p><p class="mt-1 text-sm text-[color:var(--muted)]">每天服务器执行，失败原因留记录。</p></div>
        </div>
        <div class="mt-6 rounded-[1.6rem] border border-[color:var(--line)] bg-white/60 p-4">
          <p class="text-sm text-[color:var(--muted)]">今日状态</p>
          <div class="mt-3 flex items-end justify-between"><span class="text-4xl font-extrabold">稳定运行中...</span><iconify-icon icon="ph:list-checks-bold" width="42" height="42" style="color:var(--mint)"></iconify-icon></div>
        </div>
      ''', 'lg:mt-16')}
    </section>
    """


def dashboard(user):
    accounts = query("SELECT * FROM accounts WHERE user_id=? ORDER BY id DESC", (user["id"],))
    rows = []
    for account in accounts:
        confirmed = query_one("SELECT COUNT(*) c FROM forums WHERE account_id=? AND is_confirmed=1 AND is_enabled=1", (account["id"],))["c"]
        total = query_one("SELECT COUNT(*) c FROM forums WHERE account_id=? AND is_followed=1", (account["id"],))["c"]
        success = query_one("SELECT COUNT(*) c FROM sign_records WHERE account_id=? AND sign_date=? AND status='success'", (account["id"], today_key()))["c"]
        rows.append(f"""
        <article class="rounded-[1.5rem] bg-[#f4fbf7] p-4">
          <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div><h3 class="text-xl font-extrabold">{esc(account['label'])}</h3><p class="mt-1 text-sm text-[color:var(--muted)]">状态：{esc(account['status'])} · 已确认 {confirmed}/{total} · 今日成功 {success}</p></div>
            <div class="flex flex-wrap gap-2">
              <a class="focusable rounded-xl border border-[color:var(--line)] px-3 py-2 text-sm font-bold" href="/account/{account['id']}/forums">确认贴吧</a>
              <form method="post" action="/account/{account['id']}/sync"><button class="focusable cursor-pointer rounded-xl border border-[color:var(--line)] px-3 py-2 text-sm font-bold">同步关注</button></form>
              <form method="post" action="/account/{account['id']}/sign"><button class="focusable cursor-pointer rounded-xl bg-[color:var(--orange)] px-3 py-2 text-sm font-extrabold text-[#2a1207]">手动签到</button></form>
              <form method="post" action="/account/{account['id']}/delete" onsubmit="return confirm('删除这个百度账号？相关贴吧和签到记录也会删除。')"><button class="focusable cursor-pointer rounded-xl border border-[#e45d3b]/50 px-3 py-2 text-sm font-bold text-[#ff8b6f]">删除账号</button></form>
            </div>
          </div>
        </article>""")
    records = query("SELECT * FROM sign_records WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (user["id"],))
    record_rows = "".join(f"<tr class='border-t border-[#d9e9e2]'><td class='py-2'>{esc(r['forum_name'])}</td><td>{esc(r['status'])}</td><td>{esc(r['message'])}</td><td>{ts(r['created_at'])}</td></tr>" for r in records) or "<tr><td class='py-3 text-[color:var(--muted)]' colspan='4'>还没有日志。</td></tr>"
    return f"""
    <div class="grid gap-5 lg:grid-cols-[.72fr_1.28fr]">
      {card('<p class="text-sm uppercase tracking-[.24em] text-[color:var(--mint)]">BDUSS</p><h1 class="mt-3 text-3xl font-extrabold">添加百度账号</h1><form class="mt-6 grid gap-4" method="post" action="/account/add"><label class="grid gap-2 text-sm font-bold">备注<input class="focusable h-12 rounded-2xl px-4" name="label" placeholder="比如：主号"></label><label class="grid gap-2 text-sm font-bold">BDUSS<textarea class="focusable min-h-28 rounded-2xl p-4" name="bduss" required placeholder="只填 BDUSS 值，不要填 Cookie=..."></textarea></label><button class="focusable h-12 cursor-pointer rounded-2xl bg-[color:var(--mint)] font-extrabold text-[#f7fffb]">保存并同步</button></form>')}
      {card('<p class="text-sm text-[color:var(--muted)]">账号</p><h2 class="text-2xl font-extrabold">先同步，再确认。</h2><div class="mt-5 grid gap-3">' + (''.join(rows) or '<p class="text-[color:var(--muted)]">还没添加账号。</p>') + '</div>')}
    </div>
    {card('<p class="text-sm text-[color:var(--muted)]">最近签到</p><div class="mt-4 overflow-x-auto"><table class="w-full min-w-[680px] text-left text-sm"><thead><tr class="text-[color:var(--muted)]"><th class="py-2">贴吧</th><th>状态</th><th>说明</th><th>时间</th></tr></thead><tbody>' + record_rows + '</tbody></table></div>', 'mt-5')}
    """


def forums_page(user, account_id):
    account = query_one("SELECT * FROM accounts WHERE id=? AND user_id=?", (account_id, user["id"]))
    if not account:
        return card("<h1 class='text-3xl font-extrabold'>账号不存在。</h1>")
    forums = query("SELECT * FROM forums WHERE account_id=? AND is_followed=1 ORDER BY name", (account_id,))
    checks = "".join(f"""
      <label class="flex items-center justify-between gap-3 rounded-[1.2rem] bg-[#f4fbf7] p-3">
        <span><span class="font-bold">{esc(f['name'])}</span><span class="ml-2 text-xs text-[color:var(--muted)]">fid {esc(f['forum_id'])}</span></span>
        <input class="forum-check h-5 w-5" type="checkbox" value="{esc(f['forum_id'])}" {'checked' if f['is_enabled'] else ''}>
      </label>""" for f in forums)
    return card(f"""
      <p class="text-sm uppercase tracking-[.24em] text-[color:var(--mint)]">Confirm</p>
      <h1 class="mt-3 text-3xl font-extrabold">确认要签到的吧</h1>
      <p class="mt-3 text-[color:var(--muted)]">账号：{esc(account['label'])}。只勾你真想签的。</p>
      <form class="mt-6 grid gap-3" method="post" action="/account/{account_id}/confirm" onsubmit="document.getElementById('forums').value=[...document.querySelectorAll('.forum-check:checked')].map(i=>i.value).join(',')">
        <input id="forums" name="forums" type="hidden">
        {checks or '<p class="text-[color:var(--muted)]">还没同步到关注贴吧。</p>'}
        <button class="focusable mt-3 h-12 cursor-pointer rounded-2xl bg-[color:var(--orange)] font-extrabold text-[#2a1207]">确认名单</button>
      </form>
    """)


def admin_page():
    users = query("SELECT id, username, role, status, created_at FROM users ORDER BY id DESC")
    accounts = query("SELECT accounts.*, users.username FROM accounts JOIN users ON users.id=accounts.user_id ORDER BY accounts.id DESC")
    today = today_key()
    stats = query_one("SELECT COUNT(*) total, SUM(status='success') success, SUM(status='failed') failed, SUM(status LIKE 'paused%') paused FROM sign_records WHERE sign_date=?", (today,))
    password_dialogs = []
    user_rows = []
    for u in users:
        password_button = ""
        if u["role"] == "user":
            password_button = f"<button class='focusable cursor-pointer rounded-xl border border-[color:var(--line)] px-3 py-1 text-sm' type='button' onclick=\"document.getElementById('password-dialog-{u['id']}').showModal()\">改密码</button>"
            password_dialogs.append(f"""
      <dialog id='password-dialog-{u['id']}' class='rounded-[2rem] border border-[color:var(--line)] bg-white p-0 text-[color:var(--paper)] shadow-2xl backdrop:bg-[#12312c]/35'>
        <form class='grid w-[min(92vw,26rem)] gap-4 p-6' method='post' action='/admin/user/{u['id']}/password'>
          <div>
            <p class='text-sm uppercase tracking-[.24em] text-[color:var(--mint)]'>Password</p>
            <h3 class='mt-2 text-2xl font-extrabold'>修改 {esc(u['username'])} 的密码</h3>
            <p class='mt-2 text-sm text-[color:var(--muted)]'>保存后该用户需要重新登录。</p>
          </div>
          <label class='grid gap-2 text-sm font-bold'>新密码<input class='focusable h-12 rounded-2xl px-4' name='new_password' type='password' minlength='6' required></label>
          <div class='flex justify-end gap-2'>
            <button class='focusable cursor-pointer rounded-xl border border-[color:var(--line)] px-4 py-2 text-sm font-bold' type='button' onclick=\"document.getElementById('password-dialog-{u['id']}').close()\">取消</button>
            <button class='focusable cursor-pointer rounded-xl bg-[color:var(--orange)] px-4 py-2 text-sm font-extrabold text-[#2a1207]' type='submit'>保存</button>
          </div>
        </form>
      </dialog>""")
        else:
            password_button = "<button class='rounded-xl border border-[color:var(--line)] px-3 py-1 text-sm opacity-50' disabled>改密码</button>"
        user_rows.append(f"""
      <tr class='border-t border-[#d9e9e2]'>
        <td class='py-2'>{u['id']}</td><td>{esc(u['username'])}</td><td>{esc(u['role'])}</td><td>{esc(u['status'])}</td><td>{ts(u['created_at'])}</td>
        <td class='flex flex-wrap gap-2 py-2'>
          <form method='post' action='/admin/user/{u['id']}/toggle'><button class='focusable cursor-pointer rounded-xl border border-[color:var(--line)] px-3 py-1 text-sm'>{'恢复' if u['status'] != 'active' else '封禁'}</button></form>
          {password_button}
          <form method='post' action='/admin/user/{u['id']}/delete' onsubmit="return confirm('删除这个用户？该用户的百度账号、贴吧和签到记录也会删除。')"><button class='focusable cursor-pointer rounded-xl border border-[#e45d3b]/50 px-3 py-1 text-sm font-bold text-[#d94f2f]'>删除</button></form>
        </td>
      </tr>""")
    user_rows = "".join(user_rows)
    password_dialogs = "".join(password_dialogs)
    account_rows = "".join(f"""
      <tr class='border-t border-[#d9e9e2]'>
        <td class='py-2'>{a['id']}</td><td>{esc(a['username'])}</td><td>{esc(a['label'])}</td><td>{esc(a['status'])}</td><td>{ts(a['last_sync_at'])}</td><td>{ts(a['last_sign_at'])}</td>
        <td><form method='post' action='/admin/account/{a['id']}/toggle'><button class='focusable cursor-pointer rounded-xl border border-[color:var(--line)] px-3 py-1 text-sm'>暂停/恢复</button></form></td>
      </tr>""" for a in accounts)
    logs = query("SELECT audit_logs.*, users.username FROM audit_logs LEFT JOIN users ON users.id=audit_logs.user_id ORDER BY audit_logs.id DESC LIMIT 30")
    log_rows = "".join(f"<tr class='border-t border-[#d9e9e2]'><td class='py-2'>{ts(l['created_at'])}</td><td>{esc(l['username'] or 'system')}</td><td>{esc(l['action'])}</td><td>{esc(l['detail'])}</td></tr>" for l in logs)
    return f"""
    <div class="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-sm uppercase tracking-[.24em] text-[color:var(--mint)]">Admin</p>
        <h1 class="mt-2 text-3xl font-extrabold tracking-[-.04em]">管理员后台</h1>
      </div>
      <span class="rounded-2xl border border-[color:var(--line)] bg-white/60 px-4 py-2 text-sm font-bold text-[color:var(--muted)]">当前版本：{esc(APP_VERSION)}</span>
    </div>
    <div class="grid gap-5 md:grid-cols-4">
      {card(f'<p class="text-sm text-[color:var(--muted)]">今日任务</p><p class="mt-2 text-3xl font-extrabold">{stats["total"] or 0}</p>')}
      {card(f'<p class="text-sm text-[color:var(--muted)]">成功</p><p class="mt-2 text-3xl font-extrabold text-[color:var(--mint)]">{stats["success"] or 0}</p>')}
      {card(f'<p class="text-sm text-[color:var(--muted)]">失败</p><p class="mt-2 text-3xl font-extrabold text-[color:var(--orange)]">{stats["failed"] or 0}</p>')}
      {card(f'<p class="text-sm text-[color:var(--muted)]">暂停</p><p class="mt-2 text-3xl font-extrabold">{stats["paused"] or 0}</p>')}
    </div>
    {card('<h2 class="text-2xl font-extrabold">用户</h2><div class="mt-4 overflow-x-auto"><table class="w-full min-w-[820px] text-left text-sm"><thead class="text-[color:var(--muted)]"><tr><th>ID</th><th>用户名</th><th>角色</th><th>状态</th><th>创建</th><th>操作</th></tr></thead><tbody>' + user_rows + '</tbody></table></div>', 'mt-5')}
    {card('<h2 class="text-2xl font-extrabold">账号</h2><div class="mt-4 overflow-x-auto"><table class="w-full min-w-[760px] text-left text-sm"><thead class="text-[color:var(--muted)]"><tr><th>ID</th><th>用户</th><th>备注</th><th>状态</th><th>同步</th><th>签到</th><th>操作</th></tr></thead><tbody>' + account_rows + '</tbody></table></div>', 'mt-5')}
    {card('<h2 class="text-2xl font-extrabold">审计日志</h2><div class="mt-4 overflow-x-auto"><table class="w-full min-w-[760px] text-left text-sm"><thead class="text-[color:var(--muted)]"><tr><th>时间</th><th>用户</th><th>动作</th><th>详情</th></tr></thead><tbody>' + log_rows + '</tbody></table></div>', 'mt-5')}
    {password_dialogs}
    """


def main():
    init_db()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), App)
    print(f"Tieba sign-in web is running: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
