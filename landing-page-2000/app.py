"""RK Work Portal — 포털 백엔드(FastAPI).

역할:
- 로그인/회원가입(승인제) + 관리자 회원 승인 관리
- 포털 홈(WebOCR / CafeShipment / 키 설정 카드)을 서버에서 직접 서빙
- 회원/세션은 서버 SQLite(./data/portal.db)에 저장. 비밀번호는 pbkdf2 해시.

원칙(서버 작업 규칙):
- 비밀번호/시크릿은 서버 .env·DB에만. 화면/로그로 평문 노출 금지.
- 기능 추가는 이 앱에 라우트/카드로 확장(각 페이지는 이후 보강).
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

DB_PATH = os.environ.get("PORTAL_DB", "/data/portal.db")
if not os.path.isdir(os.path.dirname(DB_PATH) or "."):
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "portal.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

ADMIN_EMAIL = (os.environ.get("PORTAL_ADMIN_EMAIL") or "").strip().lower()
ADMIN_PASSWORD = os.environ.get("PORTAL_ADMIN_PASSWORD") or ""
HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="RK Work Portal", version="1.0.0")


# ── DB ──
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                pw_hash TEXT NOT NULL,
                name TEXT DEFAULT '',
                org TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS login_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                email TEXT,
                login_at TEXT NOT NULL,
                logout_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS markets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                alias TEXT NOT NULL,
                market_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "event_id" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN event_id INTEGER")
        conn.commit()
        if ADMIN_EMAIL and ADMIN_PASSWORD:
            row = conn.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO users(email,pw_hash,name,org,status,is_admin,created_at) VALUES(?,?,?,?,?,?,?)",
                    (ADMIN_EMAIL, hash_pw(ADMIN_PASSWORD), "관리자", "RK", "active", 1, now()),
                )
                conn.commit()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def hash_pw(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${digest}"


def verify_pw(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
        return secrets.compare_digest(check, digest)
    except Exception:
        return False


# ── 세션 ──
def current_user(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get("sid")
    if not token:
        return None
    with db() as conn:
        s = conn.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
        if not s:
            return None
        return conn.execute("SELECT * FROM users WHERE id=?", (s["user_id"],)).fetchone()


def require_user(request: Request) -> sqlite3.Row:
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return u


def require_admin(request: Request) -> sqlite3.Row:
    u = require_user(request)
    if not u["is_admin"]:
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return u


def user_public(u: sqlite3.Row) -> dict:
    return {
        "id": u["id"], "email": u["email"], "name": u["name"], "org": u["org"],
        "status": u["status"], "isAdmin": bool(u["is_admin"]),
        "active": u["status"] == "active" or bool(u["is_admin"]),
    }


# ── 스키마 ──
class RegisterBody(BaseModel):
    email: str
    password: str
    name: str = ""
    org: str = ""


class LoginBody(BaseModel):
    email: str
    password: str


class ProfileBody(BaseModel):
    name: str = ""
    org: str = ""


class PasswordBody(BaseModel):
    currentPassword: str
    newPassword: str


class MarketBody(BaseModel):
    alias: str
    market_type: str


# ── 라우트 ──
@app.on_event("startup")
def _startup():
    init_db()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index():
    path = os.path.join(HERE, "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/api/register")
def register(body: RegisterBody):
    email = body.email.strip().lower()
    if not email or not body.password:
        raise HTTPException(status_code=400, detail="이메일과 비밀번호를 입력하세요.")
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.")
        conn.execute(
            "INSERT INTO users(email,pw_hash,name,org,status,is_admin,created_at) VALUES(?,?,?,?,?,?,?)",
            (email, hash_pw(body.password), body.name.strip(), body.org.strip(), "pending", 0, now()),
        )
        conn.commit()
    return {"ok": True, "status": "pending"}


@app.post("/api/login")
def login(body: LoginBody, response: Response):
    email = body.email.strip().lower()
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u or not verify_pw(body.password, u["pw_hash"]):
            raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
        if u["status"] == "disabled":
            raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")
        token = secrets.token_urlsafe(32)
        ev = conn.execute("INSERT INTO login_events(user_id,email,login_at) VALUES(?,?,?)", (u["id"], u["email"], now()))
        conn.execute("INSERT INTO sessions(token,user_id,created_at,event_id) VALUES(?,?,?,?)", (token, u["id"], now(), ev.lastrowid))
        conn.commit()
    response.set_cookie("sid", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)
    return {"ok": True, "user": user_public(u)}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("sid")
    if token:
        with db() as conn:
            row = conn.execute("SELECT event_id FROM sessions WHERE token=?", (token,)).fetchone()
            if row and row["event_id"]:
                conn.execute("UPDATE login_events SET logout_at=? WHERE id=? AND logout_at IS NULL", (now(), row["event_id"]))
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.commit()
    response.delete_cookie("sid")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    u = current_user(request)
    return {"ok": True, "user": user_public(u) if u else None}


# ── 관리자: 회원 승인 관리 ──
@app.get("/api/admin/members")
def admin_members(request: Request, status: str = ""):
    require_admin(request)
    q = "SELECT * FROM users"
    params: tuple = ()
    if status in ("pending", "active", "disabled"):
        q += " WHERE status=?"
        params = (status,)
    q += " ORDER BY created_at DESC"
    with db() as conn:
        rows = conn.execute(q, params).fetchall()
        counts = {r["status"]: r["c"] for r in conn.execute("SELECT status, COUNT(*) c FROM users GROUP BY status")}
    members = [
        {"id": r["id"], "email": r["email"], "name": r["name"], "org": r["org"],
         "status": r["status"], "isAdmin": bool(r["is_admin"]), "createdAt": r["created_at"]}
        for r in rows
    ]
    total = sum(counts.values())
    return {"ok": True, "members": members,
            "counts": {"total": total, "pending": counts.get("pending", 0),
                       "active": counts.get("active", 0), "disabled": counts.get("disabled", 0)}}


def _set_status(request: Request, user_id: int, status: str):
    admin = require_admin(request)
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다.")
        if u["is_admin"] and status == "disabled":
            raise HTTPException(status_code=400, detail="관리자 계정은 비활성화할 수 없습니다.")
        conn.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))
        conn.commit()
    return {"ok": True, "id": user_id, "status": status}


@app.post("/api/admin/members/{user_id}/approve")
def approve(user_id: int, request: Request):
    return _set_status(request, user_id, "active")


@app.post("/api/admin/members/{user_id}/reject")
def reject(user_id: int, request: Request):
    return _set_status(request, user_id, "disabled")


@app.post("/api/admin/members/{user_id}/disable")
def disable(user_id: int, request: Request):
    return _set_status(request, user_id, "disabled")


@app.post("/api/admin/members/{user_id}/restore")
def restore(user_id: int, request: Request):
    return _set_status(request, user_id, "active")


# ── 회원정보 변경 ──
@app.post("/api/profile")
def update_profile(body: ProfileBody, request: Request):
    u = require_user(request)
    with db() as conn:
        conn.execute("UPDATE users SET name=?, org=? WHERE id=?", (body.name.strip(), body.org.strip(), u["id"]))
        conn.commit()
        u = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    return {"ok": True, "user": user_public(u)}


@app.post("/api/change-password")
def change_password(body: PasswordBody, request: Request):
    u = require_user(request)
    if not verify_pw(body.currentPassword, u["pw_hash"]):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    if len(body.newPassword) < 4:
        raise HTTPException(status_code=400, detail="새 비밀번호가 너무 짧습니다.")
    with db() as conn:
        conn.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_pw(body.newPassword), u["id"]))
        conn.commit()
    return {"ok": True}


# ── 마켓(1인 다(多)몰) ──
@app.get("/api/markets")
def list_markets(request: Request):
    u = require_user(request)
    with db() as conn:
        rows = conn.execute("SELECT * FROM markets WHERE user_id=? ORDER BY created_at", (u["id"],)).fetchall()
    return {"ok": True, "markets": [
        {"id": r["id"], "alias": r["alias"], "type": r["market_type"], "createdAt": r["created_at"]} for r in rows]}


@app.post("/api/markets")
def add_market(body: MarketBody, request: Request):
    u = require_user(request)
    if not u["status"] == "active" and not u["is_admin"]:
        raise HTTPException(status_code=403, detail="승인 후 마켓을 추가할 수 있습니다.")
    if not body.alias.strip() or not body.market_type.strip():
        raise HTTPException(status_code=400, detail="마켓 이름과 종류를 입력하세요.")
    with db() as conn:
        conn.execute("INSERT INTO markets(user_id,alias,market_type,created_at) VALUES(?,?,?,?)",
                     (u["id"], body.alias.strip(), body.market_type.strip(), now()))
        conn.commit()
    return {"ok": True}


@app.delete("/api/markets/{market_id}")
def delete_market(market_id: int, request: Request):
    u = require_user(request)
    with db() as conn:
        m = conn.execute("SELECT user_id FROM markets WHERE id=?", (market_id,)).fetchone()
        if not m or m["user_id"] != u["id"]:
            raise HTTPException(status_code=404, detail="마켓을 찾을 수 없습니다.")
        conn.execute("DELETE FROM markets WHERE id=?", (market_id,))
        conn.commit()
    return {"ok": True}


# ── 관리자: 접속 기록 ──
@app.get("/api/admin/access-log")
def access_log(request: Request, limit: int = 100):
    require_admin(request)
    with db() as conn:
        rows = conn.execute(
            """SELECT e.id, e.email, e.login_at, e.logout_at, u.name
               FROM login_events e LEFT JOIN users u ON u.id=e.user_id
               ORDER BY e.login_at DESC LIMIT ?""", (max(1, min(limit, 500)),)).fetchall()
    return {"ok": True, "events": [
        {"id": r["id"], "name": r["name"], "email": r["email"],
         "loginAt": r["login_at"], "logoutAt": r["logout_at"]} for r in rows]}
