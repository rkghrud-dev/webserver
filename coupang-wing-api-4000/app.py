import gzip
import hashlib
import hmac
import html
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode
import re

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

load_dotenv()

app = FastAPI(title="WebOCR Key Manager")

CLIENT_TOKEN = os.getenv("CLIENT_TOKEN", "")
ADMIN_ID = os.getenv("ADMIN_ID", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
KEY_MANAGER_ROOT = Path(os.getenv("KEY_MANAGER_ROOT", os.getenv("KEY_ROOT", "/data/key-manager")))
KEY_ROOT = KEY_MANAGER_ROOT
COUPANG_BASE_URL = os.getenv("COUPANG_BASE_URL", "https://api-gateway.coupang.com")
DB_PATH = Path(os.getenv("KEY_MANAGER_DB", str(KEY_MANAGER_ROOT / "key_manager.db")))
_DB_READY = False

COUPANG_ACCOUNTS = {
    "home": {
        "label": "홈런 / A 계정",
        "path": Path("홈런") / "쿠팡" / "coupang_wing_api.txt",
    },
    "ready": {
        "label": "준비 / B 계정",
        "path": Path("준비") / "쿠팡" / "coupang_api_junbi.txt",
    },
}


@app.on_event("startup")
def startup() -> None:
    init_db()


class ProductNameRequest(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=300)
    account: str = Field("home", description="home or ready")


class CategoryMetaRequest(BaseModel):
    category_code: int = Field(..., ge=1)
    account: str = Field("home", description="home or ready")


class CoupangProxyRequest(BaseModel):
    method: str = Field("GET", description="GET, POST, PUT, PATCH, or DELETE")
    path: str = Field(..., description="Coupang API path only, not a full URL")
    account: str = Field("home", description="home or ready")
    query: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] | list[Any] | None = None


class KeyFileItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    text: str = ""


class SaveKeyFilesRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    shop_name: str = Field(..., min_length=1, max_length=80)
    files: list[KeyFileItem] = Field(default_factory=list)


def require_client_token(x_client_token: str | None) -> None:
    if not CLIENT_TOKEN or x_client_token != CLIENT_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_admin_password(password: str) -> None:
    if not ADMIN_PASSWORD or not hmac.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_admin_credentials(admin_id: str, password: str) -> None:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin password is not configured")
    if not hmac.compare_digest(admin_id, ADMIN_ID) or not hmac.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")


def safe_segment(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        raise HTTPException(status_code=400, detail="Invalid name")
    return cleaned[:80]


def safe_relative_path(value: str) -> Path:
    normalized = value.replace("\\", "/").strip("/")
    parts = [safe_segment(part) for part in normalized.split("/") if part not in {"", ".", ".."}]
    if not parts:
        raise HTTPException(status_code=400, detail="Invalid file path")
    return Path(*parts)


def shop_root(owner_name: str, shop_name: str) -> Path:
    return KEY_MANAGER_ROOT / get_or_create_shop_id(owner_name, shop_name)


def registry_path() -> Path:
    return KEY_MANAGER_ROOT / "shop_registry.json"


def db_connect() -> sqlite3.Connection:
    KEY_MANAGER_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    global _DB_READY
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_code TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                market_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, market_name),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        for table in ("users", "markets"):
            cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if "status" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_markets_user_id ON markets(user_id)")
    _DB_READY = True
    migrate_registry_json_to_db()


def ensure_db() -> None:
    if not _DB_READY:
        init_db()


def load_shop_registry() -> dict[str, Any]:
    ensure_db()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT m.market_code AS shop_id, u.name AS owner_name, m.market_name AS shop_name,
                   u.status AS user_status, m.status AS market_status,
                   m.created_at, m.updated_at
            FROM markets m
            JOIN users u ON u.id = m.user_id
            ORDER BY m.market_code
            """
        ).fetchall()
    return {"shops": [dict(row) for row in rows]}


def save_shop_registry(registry: dict[str, Any]) -> None:
    for shop in registry.get("shops", []):
        owner = shop.get("owner_name") or shop.get("name") or "unknown"
        market = shop.get("shop_name") or shop.get("market_name")
        code = shop.get("shop_id") or shop.get("market_code")
        if market and code:
            upsert_market(owner, market, preferred_code=code)


def next_market_code(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT market_code FROM markets").fetchall()
    used = [
        int(str(row["market_code"])[1:])
        for row in rows
        if re.fullmatch(r"S\d{3}", str(row["market_code"]))
    ]
    return f"S{(max(used) if used else 0) + 1:03d}"


def get_or_create_user(conn: sqlite3.Connection, name: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute("INSERT INTO users(name, status, created_at) VALUES(?, 'pending', ?)", (name, now))
    return int(cur.lastrowid)


def upsert_market(owner_name: str, market_name: str, preferred_code: str | None = None) -> str:
    ensure_db()
    raw_owner = owner_name.strip()
    raw_market = market_name.strip()
    if not raw_owner:
        raise HTTPException(status_code=400, detail="Invalid owner name")
    if not raw_market:
        raise HTTPException(status_code=400, detail="Invalid market name")
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as conn:
        user_id = get_or_create_user(conn, raw_owner)
        row = conn.execute(
            "SELECT market_code FROM markets WHERE user_id=? AND market_name=?",
            (user_id, raw_market),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE markets SET updated_at=? WHERE market_code=?",
                (now, row["market_code"]),
            )
            return str(row["market_code"])
        code = preferred_code if preferred_code and re.fullmatch(r"S\d{3}", preferred_code) else next_market_code(conn)
        if conn.execute("SELECT 1 FROM markets WHERE market_code=?", (code,)).fetchone():
            code = next_market_code(conn)
        conn.execute(
            "INSERT INTO markets(market_code, user_id, market_name, status, created_at, updated_at) VALUES(?, ?, ?, 'pending', ?, ?)",
            (code, user_id, raw_market, now, now),
        )
        return code


def migrate_registry_json_to_db() -> None:
    marker = KEY_MANAGER_ROOT / ".registry_migrated"
    path = registry_path()
    if marker.exists() or not path.exists():
        return
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        marker.write_text("failed\n", encoding="utf-8")
        return
    for shop in registry.get("shops", []):
        owner = shop.get("owner_name") or shop.get("name") or "unknown"
        market = shop.get("shop_name") or shop.get("market_name")
        code = shop.get("shop_id") or shop.get("market_code")
        if market:
            raw_owner = str(owner).strip() or "unknown"
            raw_market = str(market).strip()
            if not raw_market:
                continue
            now = datetime.now(timezone.utc).isoformat()
            with db_connect() as conn:
                user_id = get_or_create_user(conn, raw_owner)
                exists = conn.execute(
                    "SELECT 1 FROM markets WHERE user_id=? AND market_name=?",
                    (user_id, raw_market),
                ).fetchone()
                if exists:
                    continue
                market_code = code if code and re.fullmatch(r"S\d{3}", str(code)) else next_market_code(conn)
                if conn.execute("SELECT 1 FROM markets WHERE market_code=?", (market_code,)).fetchone():
                    market_code = next_market_code(conn)
                conn.execute(
                    "INSERT INTO markets(market_code, user_id, market_name, status, created_at, updated_at) VALUES(?, ?, ?, 'pending', ?, ?)",
                    (market_code, user_id, raw_market, now, now),
                )
    marker.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")


def get_or_create_shop_id(owner_name: str, shop_name: str) -> str:
    return upsert_market(owner_name, shop_name)


def save_key_files(payload: SaveKeyFilesRequest) -> dict[str, Any]:
    shop_id = get_or_create_shop_id(payload.name, payload.shop_name)
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT u.status AS user_status, m.status AS market_status
            FROM markets m
            JOIN users u ON u.id = m.user_id
            WHERE m.market_code=?
            """,
            (shop_id,),
        ).fetchone()
    if not row or row["user_status"] != "active" or row["market_status"] != "active":
        raise HTTPException(status_code=403, detail="관리자 승인 후 키를 저장할 수 있습니다.")
    root = KEY_MANAGER_ROOT / shop_id
    root.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for item in payload.files:
        rel = safe_relative_path(item.name)
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item.text.encode("utf-8"))
        try:
            path.chmod(0o600)
        except OSError:
            pass
        saved.append(str(rel).replace("\\", "/"))

    manifest = {
        "name": payload.name.strip(),
        "owner_name": payload.name.strip(),
        "shop_name": payload.shop_name.strip(),
        "shop_id": shop_id,
        "saved_count": len(saved),
        "files": saved,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (root / "key_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "shop_id": shop_id, "saved_count": len(saved), "files": saved}


def list_admin_rows() -> list[dict[str, Any]]:
    ensure_db()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT u.id AS user_id, u.name, u.status AS user_status,
                   m.id AS market_id, m.market_code, m.market_name, m.status AS market_status,
                   m.created_at, m.updated_at
            FROM markets m
            JOIN users u ON u.id = m.user_id
            ORDER BY m.created_at DESC, m.market_code DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_user_markets(name: str) -> list[dict[str, Any]]:
    ensure_db()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT u.name, u.status AS user_status,
                   m.id AS market_id, m.market_code, m.market_name, m.status AS market_status,
                   m.created_at, m.updated_at
            FROM markets m
            JOIN users u ON u.id = m.user_id
            WHERE u.name=?
            ORDER BY m.market_code
            """,
            (name.strip(),),
        ).fetchall()
    return [dict(row) for row in rows]


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def portal_shell(title: str, body: str, *, name: str = "", admin: bool = False) -> str:
    display_name = esc(name)
    admin_link = "<a href='/admin/login'>관리자</a>" if not name and not admin else ""
    if admin:
        admin_link = "<a href='/portal/login'>로그아웃</a>"
    user_links = ""
    if name:
        quoted_name = quote(name)
        user_links = (
            f"<a href='/portal/dashboard?name={quoted_name}'>홈</a>"
            f"<a href='/apps/webocr?name={quoted_name}'>WebOCR</a>"
            f"<a href='/apps/cafeshipment?name={quoted_name}'>CafeShipment</a>"
            f"<a href='/keys/login'>키 설정</a>"
        )
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eef2f7;
      --surface: #ffffff;
      --surface-2: #f7f9fc;
      --ink: #172033;
      --muted: #657184;
      --line: #dce3ee;
      --blue: #2457d6;
      --green: #178b61;
      --amber: #9a6500;
      --red: #b8293d;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: Arial, 'Malgun Gothic', sans-serif; background: var(--bg); color: var(--ink); }}
    a {{ color: inherit; }}
    .top {{ min-height: 64px; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 0 24px; background: var(--surface); border-bottom: 1px solid var(--line); }}
    .brand {{ display: flex; align-items: center; gap: 10px; font-weight: 800; }}
    .mark {{ width: 34px; height: 34px; display: grid; place-items: center; border-radius: 8px; background: var(--blue); color: white; }}
    .nav {{ display: flex; align-items: center; gap: 14px; color: var(--muted); font-size: 14px; }}
    .nav a {{ text-decoration: none; font-weight: 700; }}
    .wrap {{ width: min(1120px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 44px; }}
    .panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 22px; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .card {{ min-height: 170px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 18px; display: flex; flex-direction: column; justify-content: space-between; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; letter-spacing: 0; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.55; }}
    .meta {{ margin-top: 6px; color: var(--muted); font-size: 14px; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }}
    .btn, button {{ min-height: 40px; display: inline-flex; align-items: center; justify-content: center; padding: 0 13px; border-radius: 8px; border: 1px solid var(--line); background: white; color: var(--ink); font-weight: 800; text-decoration: none; cursor: pointer; }}
    .btn.primary, button.primary {{ background: var(--blue); color: white; border-color: var(--blue); }}
    .status {{ display: inline-flex; align-items: center; min-height: 28px; padding: 0 9px; border-radius: 8px; font-size: 13px; font-weight: 800; }}
    .active {{ background: #e7f6ef; color: var(--green); }}
    .pending {{ background: #fff5dc; color: var(--amber); }}
    .disabled {{ background: #ffe8ec; color: var(--red); }}
    form.stack {{ display: grid; gap: 12px; max-width: 440px; }}
    label {{ display: grid; gap: 6px; font-weight: 800; font-size: 14px; }}
    input {{ width: 100%; min-height: 42px; padding: 9px 11px; border: 1px solid #cfd7e6; border-radius: 8px; font-size: 15px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 12px; border-bottom: 1px solid #edf0f5; text-align: left; font-size: 14px; }}
    th {{ background: var(--surface-2); color: #33405a; }}
    .empty {{ color: var(--muted); text-align: center; padding: 28px; }}
    @media (max-width: 820px) {{
      .top {{ align-items: flex-start; flex-direction: column; padding: 16px; }}
      .nav {{ flex-wrap: wrap; }}
      .grid {{ grid-template-columns: 1fr; }}
      table {{ display: block; overflow-x: auto; }}
    }}
  </style>
</head>
<body>
  <header class="top">
    <div class="brand"><div class="mark">RK</div><span>RK Work Portal</span></div>
    <nav class="nav">{user_links}{admin_link}<span>{display_name}</span></nav>
  </header>
  <main class="wrap">{body}</main>
</body>
</html>"""


def status_badge(value: str) -> str:
    status = value if value in {"active", "pending", "disabled"} else "pending"
    label = {"active": "승인됨", "pending": "승인 대기", "disabled": "비활성"}[status]
    return f"<span class='status {status}'>{label}</span>"


def user_dashboard(name: str) -> str:
    markets = list_user_markets(name)
    quoted_name = quote(name)
    market_rows = "".join(
        f"<tr><td>{esc(row['market_code'])}</td><td>{esc(row['market_name'])}</td>"
        f"<td>{status_badge(row['user_status'])}</td><td>{status_badge(row['market_status'])}</td>"
        f"<td><a class='btn' href='/keys/manage?name={quoted_name}&shop_name={quote(str(row['market_name']))}'>키 설정</a></td></tr>"
        for row in markets
    )
    if not market_rows:
        market_rows = "<tr><td colspan='5' class='empty'>아직 등록된 마켓이 없습니다. 회원가입에서 첫 마켓을 등록하세요.</td></tr>"
    body = f"""
    <section class="panel">
      <h1>{esc(name)}님 작업 홈</h1>
      <p>WebOCR, CafeShipment, 키 설정을 한 화면에서 들어가는 포털입니다. 키 저장은 관리자가 승인한 회원과 마켓만 가능합니다.</p>
      <div class="actions">
        <a class="btn primary" href="/apps/webocr?name={quoted_name}">WebOCR 들어가기</a>
        <a class="btn" href="/apps/cafeshipment?name={quoted_name}">CafeShipment 들어가기</a>
        <a class="btn" href="/keys/login">마켓 추가 / 키 설정</a>
      </div>
    </section>
    <section class="grid" style="margin-top:14px">
      <article class="card"><div><h2>WebOCR 프로그램</h2><p>상품/마켓 작업에 필요한 OCR 기반 업무 화면으로 연결합니다.</p></div><a class="btn" href="/apps/webocr?name={quoted_name}">열기</a></article>
      <article class="card"><div><h2>CafeShipment 프로그램</h2><p>Cafe24 출고/배송 자동화 작업 공간입니다.</p></div><a class="btn" href="/apps/cafeshipment?name={quoted_name}">열기</a></article>
      <article class="card"><div><h2>키 설정 페이지</h2><p>마켓별 API 키 파일을 서버에 저장하고 관리합니다.</p></div><a class="btn" href="/keys/login">열기</a></article>
    </section>
    <section class="panel" style="margin-top:14px">
      <h2>내 마켓 권한 상태</h2>
      <table>
        <thead><tr><th>마켓 ID</th><th>마켓명</th><th>회원 상태</th><th>마켓 상태</th><th>작업</th></tr></thead>
        <tbody>{market_rows}</tbody>
      </table>
    </section>
    """
    return portal_shell("작업 홈", body, name=name)


def approve_market(market_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    ensure_db()
    with db_connect() as conn:
        row = conn.execute("SELECT user_id FROM markets WHERE id=?", (market_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Market not found")
        conn.execute("UPDATE users SET status='active' WHERE id=?", (row["user_id"],))
        conn.execute("UPDATE markets SET status='active', updated_at=? WHERE id=?", (now, market_id))


def disable_market(market_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    ensure_db()
    with db_connect() as conn:
        conn.execute("UPDATE markets SET status='disabled', updated_at=? WHERE id=?", (now, market_id))


def account_file(account: str) -> Path:
    if account not in COUPANG_ACCOUNTS:
        raise HTTPException(status_code=400, detail="Unknown Coupang account")
    return KEY_ROOT / COUPANG_ACCOUNTS[account]["path"]


def parse_legacy_key_text(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def load_coupang_secrets(account: str = "home") -> dict[str, str]:
    data = {
        "base_url": COUPANG_BASE_URL,
        "vendor_name": "",
        "url": "",
        "ip": "",
        "vendor_id": "",
        "vendor_user_id": "",
        "return_center_code": "",
        "outbound_shipping_place_code": "",
        "access_key": "",
        "secret_key": "",
        "expires_at": "",
    }
    path = account_file(account)
    if path.exists():
        data.update(parse_legacy_key_text(path.read_text(encoding="utf-8-sig")))
    return data


def legacy_coupang_text(secrets: dict[str, str]) -> str:
    ordered = [
        "vendor_name",
        "url",
        "ip",
        "vendor_id",
        "vendor_user_id",
        "return_center_code",
        "outbound_shipping_place_code",
        "access_key",
        "secret_key",
        "expires_at",
    ]
    return "".join(f"{key}={secrets.get(key, '').strip()}\r\n" for key in ordered)


def save_coupang_secrets(account: str, secrets: dict[str, str]) -> None:
    path = account_file(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    content = legacy_coupang_text(secrets).encode("utf-8")
    tmp_path.write_bytes(content)
    tmp_path.replace(path)
    flat_name = "coupang_wing_api.txt" if account == "home" else "coupang_api_junbi.txt"
    flat_path = KEY_ROOT / flat_name
    flat_path.write_bytes(content)
    try:
        path.chmod(0o600)
        flat_path.chmod(0o600)
    except OSError:
        pass


def require_coupang_config(account: str = "home") -> dict[str, str]:
    secrets = load_coupang_secrets(account)
    missing = [
        key
        for key, value in {
            "COUPANG_ACCESS_KEY": secrets["access_key"],
            "COUPANG_SECRET_KEY": secrets["secret_key"],
            "COUPANG_VENDOR_ID": secrets["vendor_id"],
        }.items()
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail={"message": "Coupang API is not configured", "missing": missing},
        )
    return secrets


def build_authorization(
    method: str,
    path: str,
    query_string: str,
    access_key: str,
    secret_key: str,
) -> str:
    signed_date = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    message = signed_date + method.upper() + path + query_string
    signature = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        "CEA algorithm=HmacSHA256, "
        f"access-key={access_key}, "
        f"signed-date={signed_date}, "
        f"signature={signature}"
    )


def validate_coupang_path(path: str) -> None:
    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail="Coupang path must start with /")
    if "://" in path or ".." in path:
        raise HTTPException(status_code=400, detail="Invalid Coupang path")
    allowed_prefixes = (
        "/v2/providers/openapi/apis/api/",
        "/v2/providers/seller_api/apis/api/",
    )
    if not any(path.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(status_code=400, detail="Coupang path is not allowed")


async def coupang_request(
    method: str,
    path: str,
    account: str = "home",
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    secrets = require_coupang_config(account)
    validate_coupang_path(path)

    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise HTTPException(status_code=400, detail="Unsupported method")

    query = query or {}
    query_string = urlencode(query, doseq=True)
    headers = {
        "Authorization": build_authorization(
            method,
            path,
            query_string,
            secrets["access_key"],
            secrets["secret_key"],
        ),
        "Content-Type": "application/json;charset=UTF-8",
        "Accept-Encoding": "gzip, identity",
        "X-EXTENDED-TIMEOUT": "90000",
    }
    if secrets["vendor_id"]:
        headers["X-Requested-By"] = secrets["vendor_id"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method,
            f"{secrets['base_url']}{path}",
            params=query,
            json=body if method != "GET" else None,
            headers=headers,
        )

    raw = response.content
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        data: Any = json.loads(raw.decode("utf-8"))
    except Exception:
        data = raw.decode("utf-8", errors="replace")

    return {"ok": response.is_success, "status_code": response.status_code, "data": data}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
def root():
    return portal_login()


@app.get("/portal/login", response_class=HTMLResponse)
def portal_login():
    body = """
    <section class="panel">
      <h1>로그인</h1>
      <p>가입한 이름으로 들어가면 WebOCR, CafeShipment, 키 설정 메뉴가 열립니다. 관리자 아이디로 로그인하면 회원 권한 관리 화면이 같이 열립니다.</p>
      <form class="stack" method="post" action="/portal/login" style="margin-top:18px">
        <label>아이디 또는 이름
          <input name="login_id" autocomplete="username" required>
        </label>
        <label>비밀번호
          <input name="password" type="password" autocomplete="current-password" placeholder="일반 회원은 비워도 됩니다">
        </label>
        <button class="primary" type="submit">로그인</button>
      </form>
      <div class="actions">
        <a class="btn" href="/portal/signup">회원가입</a>
        <a class="btn" href="/admin/login">관리자 승인 관리</a>
      </div>
    </section>
    """
    return portal_shell("로그인", body)


@app.post("/portal/login", response_class=HTMLResponse)
def portal_login_submit(login_id: str = Form(...), password: str = Form("")):
    login_id = login_id.strip()
    if not login_id:
        return RedirectResponse("/portal/login", status_code=303)
    if login_id == ADMIN_ID:
        require_admin_credentials(login_id, password)
        return admin_page(login_id, password)
    markets = list_user_markets(login_id)
    if not markets:
        quoted_name = quote(login_id)
        body = f"""
        <section class="panel">
          <h1>등록된 마켓이 없습니다</h1>
          <p>{esc(login_id)} 이름으로 회원가입을 먼저 진행해 주세요. 가입 후 관리자가 승인하면 키 저장을 사용할 수 있습니다.</p>
          <div class="actions">
            <a class="btn primary" href="/portal/signup?name={quoted_name}">회원가입</a>
            <a class="btn" href="/portal/login">다시 로그인</a>
          </div>
        </section>
        """
        return portal_shell("회원가입 필요", body)
    return user_dashboard(login_id)


@app.get("/portal/signup", response_class=HTMLResponse)
def portal_signup(name: str = ""):
    value = esc(name)
    body = f"""
    <section class="panel">
      <h1>회원가입</h1>
      <p>이름과 첫 마켓 이름을 등록하면 관리자 승인 대기 상태로 생성됩니다. 승인 후 키 설정 페이지에서 API 키 저장이 가능합니다.</p>
      <form class="stack" method="post" action="/portal/signup" style="margin-top:18px">
        <label>이름
          <input name="name" value="{value}" autocomplete="name" required>
        </label>
        <label>첫 마켓 이름
          <input name="shop_name" placeholder="예: 홈런마켓, 준비몰" required>
        </label>
        <button class="primary" type="submit">가입 신청</button>
      </form>
      <div class="actions"><a class="btn" href="/portal/login">로그인으로 돌아가기</a></div>
    </section>
    """
    return portal_shell("회원가입", body)


@app.post("/portal/signup", response_class=HTMLResponse)
def portal_signup_submit(name: str = Form(...), shop_name: str = Form(...)):
    name = name.strip()
    shop_name = shop_name.strip()
    if not name or not shop_name:
        return RedirectResponse("/portal/signup", status_code=303)
    shop_id = get_or_create_shop_id(name, shop_name)
    quoted_name = quote(name)
    body = f"""
    <section class="panel">
      <h1>가입 신청 완료</h1>
      <p>{esc(shop_name)} 마켓이 {esc(shop_id)}로 등록되었습니다. 현재는 승인 대기 상태이며, 관리자가 승인하면 키 저장을 사용할 수 있습니다.</p>
      <div class="actions">
        <a class="btn primary" href="/portal/dashboard?name={quoted_name}">내 작업 홈 보기</a>
        <a class="btn" href="/keys/manage?name={quoted_name}&shop_name={quote(shop_name)}">키 설정 미리 열기</a>
      </div>
    </section>
    """
    return portal_shell("가입 신청 완료", body, name=name)


@app.get("/portal/dashboard", response_class=HTMLResponse)
def portal_dashboard(name: str = ""):
    if not name.strip():
        return RedirectResponse("/portal/login", status_code=302)
    return user_dashboard(name.strip())


@app.get("/apps/webocr", response_class=HTMLResponse)
def webocr_app(name: str = ""):
    if not name.strip():
        return RedirectResponse("/portal/login", status_code=302)
    quoted_name = quote(name.strip())
    body = f"""
    <section class="panel">
      <h1>WebOCR 프로그램</h1>
      <p>WebOCR에서 사용하는 마켓별 키 설정과 서버 저장 상태를 확인하는 작업 공간입니다.</p>
      <div class="actions">
        <a class="btn primary" href="/portal/dashboard?name={quoted_name}">작업 홈</a>
        <a class="btn" href="/keys/login">키 설정 페이지</a>
      </div>
    </section>
    <section class="panel" style="margin-top:14px">
      <h2>연결된 마켓</h2>
      <p>내 작업 홈의 마켓 목록에서 원하는 마켓의 키 설정을 열어 저장하세요. 저장 권한은 관리자 승인 후 활성화됩니다.</p>
    </section>
    """
    return portal_shell("WebOCR", body, name=name.strip())


@app.get("/apps/cafeshipment", response_class=HTMLResponse)
def cafeshipment_app(name: str = ""):
    if not name.strip():
        return RedirectResponse("/portal/login", status_code=302)
    quoted_name = quote(name.strip())
    body = f"""
    <section class="panel">
      <h1>CafeShipment 프로그램</h1>
      <p>Cafe24 배송/출고 자동화 프로그램을 붙일 자리입니다. 지금은 포털 메뉴와 권한 구조를 먼저 잡아둔 상태입니다.</p>
      <div class="actions">
        <a class="btn primary" href="/portal/dashboard?name={quoted_name}">작업 홈</a>
        <a class="btn" href="/keys/login">키 설정 페이지</a>
      </div>
    </section>
    """
    return portal_shell("CafeShipment", body, name=name.strip())


@app.get("/index.html", response_class=HTMLResponse)
def index():
    registry = load_shop_registry()
    shops = registry.get("shops", [])
    rows = "".join(
        f"<tr><td>{shop.get('shop_id','')}</td><td>{shop.get('owner_name','')}</td><td>{shop.get('shop_name','')}</td>"
        f"<td><a href='/keys/manage?name={quote(str(shop.get('owner_name','')))}&shop_name={quote(str(shop.get('shop_name','')))}'>키 관리</a></td></tr>"
        for shop in shops
    )
    if not rows:
        rows = "<tr><td colspan='4' class='empty'>아직 등록된 마켓이 없습니다.</td></tr>"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WebOCR 키 관리</title>
  <style>
    body {{ margin:0; font-family:Arial,'Malgun Gothic',sans-serif; background:#f5f7fb; color:#202124; }}
    .wrap {{ max-width:980px; margin:0 auto; padding:34px 18px; }}
    header {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:22px; }}
    h1 {{ margin:0; font-size:24px; }}
    p {{ margin:8px 0 0; color:#5f6368; }}
    .btn {{ display:inline-block; padding:11px 15px; border-radius:8px; background:#2854c5; color:white; text-decoration:none; font-weight:700; }}
    table {{ width:100%; border-collapse:collapse; background:white; border:1px solid #dde3ee; }}
    th, td {{ padding:12px; border-bottom:1px solid #edf0f5; text-align:left; font-size:14px; }}
    th {{ background:#f0f3f9; }}
    td a {{ color:#2854c5; font-weight:700; }}
    .empty {{ color:#7b8190; text-align:center; padding:28px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>WebOCR 키 관리</h1>
        <p>이름별로 여러 마켓을 만들고, 각 마켓의 API 키 파일을 서버에 저장합니다.</p>
      </div>
      <a class="btn" href="/keys/login">회원가입</a>
    </header>
    <table>
      <thead><tr><th>마켓 ID</th><th>이름</th><th>마켓명</th><th>작업</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</body>
</html>"""


@app.get("/keys/login", response_class=HTMLResponse)
def key_login():
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WebOCR 회원가입</title>
  <style>
    body { margin:0; min-height:100vh; display:grid; place-items:center; font-family:Arial,'Malgun Gothic',sans-serif; background:#f5f7fb; color:#202124; }
    main { width:min(440px, calc(100vw - 32px)); background:white; border:1px solid #dde3ee; padding:26px; border-radius:10px; }
    h1 { margin:0 0 8px; font-size:22px; }
    p { margin:0 0 20px; color:#5f6368; font-size:14px; line-height:1.5; }
    label { display:block; margin-top:14px; font-weight:700; font-size:14px; }
    input { width:100%; box-sizing:border-box; margin-top:6px; padding:11px; font-size:15px; border:1px solid #cfd7e6; border-radius:8px; }
    button { width:100%; margin-top:20px; padding:12px; font-size:15px; font-weight:700; border:0; border-radius:8px; background:#2854c5; color:white; cursor:pointer; }
  </style>
</head>
<body>
  <main>
    <h1>회원가입</h1>
    <p>이름과 첫 마켓 이름을 입력하면 서버가 회원과 마켓 ID(S001, S002...)를 만들고, 다음 화면에서 마켓별 API 키를 저장합니다. 한 회원은 여러 마켓을 추가할 수 있습니다.</p>
    <form method="get" action="/keys/manage">
      <label>이름</label>
      <input name="name" autocomplete="name" required>
      <label>첫 마켓 이름</label>
      <input name="shop_name" placeholder="예: 홈런마켓, 테스트몰, 두번째마켓" required>
      <button type="submit">가입하고 키 설정으로 이동</button>
    </form>
  </main>
</body>
</html>"""


@app.get("/keys/manage", response_class=HTMLResponse)
def key_manage(name: str = "", shop_name: str = ""):
    if not name.strip() or not shop_name.strip():
        return RedirectResponse("/keys/login", status_code=302)
    shop_id = get_or_create_shop_id(name, shop_name)
    registry = load_shop_registry()
    owner_shops = [
        shop for shop in registry.get("shops", [])
        if shop.get("owner_name") == name.strip()
    ]
    template_path = Path(__file__).with_name("key_setup_template.html")
    html = template_path.read_text(encoding="utf-8")
    context = json.dumps(
        {
            "name": name.strip(),
            "shop_name": shop_name.strip(),
            "shop_id": shop_id,
            "shops": owner_shops,
        },
        ensure_ascii=False,
    )
    injection = f"""
<script>
window.KEY_MANAGER_CONTEXT = {context};
(function() {{
  function shopFolder() {{
    return window.KEY_MANAGER_CONTEXT.shop_name || '쇼핑몰';
  }}
  function rewriteForSingleShop() {{
    const tabs = Array.from(document.querySelectorAll('.tabbtn'));
    const panelB = document.getElementById('panel_B');
    const panelA = document.getElementById('panel_A');
    const panelCommon = document.getElementById('panel_common');
    const tabA = tabs.find(x => x.textContent.includes('A 계정'));
    const tabB = tabs.find(x => x.textContent.includes('B 계정'));
    const commonTab = tabs.find(x => x.textContent.includes('공통'));
    if (tabA) tabA.textContent = `${{shopFolder()}} 계정`;
    if (tabB) tabB.style.display = 'none';
    if (panelB) panelB.style.display = 'none';
    if (commonTab) commonTab.style.display = 'none';
    document.querySelectorAll('#panel_A .sec span:first-child').forEach(el => {{
      el.textContent = `${{shopFolder()}} - ` + el.textContent;
    }});
    if (panelA && panelCommon && !document.getElementById('server_common_inside_account')) {{
      const moved = document.createElement('div');
      moved.id = 'server_common_inside_account';
      moved.innerHTML = panelCommon.innerHTML;
      moved.querySelectorAll('.sec span:first-child').forEach(el => {{
        el.textContent = `${{shopFolder()}} - ` + el.textContent;
      }});
      panelA.appendChild(moved);
      panelCommon.classList.remove('active');
      panelCommon.style.display = 'none';
      panelA.classList.add('active');
      if (tabA) tabA.classList.add('active');
    }}
    document.body.style.paddingBottom = '32px';
    const bar = document.querySelector('.bar');
    if (bar) {{
      bar.style.position = 'static';
      bar.style.margin = '18px auto 0';
      bar.style.borderTop = '1px solid var(--line)';
    }}
  }}
  function buildServerFiles() {{
    const F = [];
    const add = (name, txt) => {{ if (txt != null) F.push({{name, text: txt}}); }};
    const folder = shopFolder();

    add('naver_client_key.txt', naver('A'));
    add('coupang_wing_api.txt', coupang('A'));
    add('lotteon_api.txt', lotte('A', true));
    add('cafe24_token.json', cafe24('A'));

    add(`${{folder}}/네이버/naver_client_key.txt`, naver('A'));
    add(`${{folder}}/쿠팡/coupang_wing_api.txt`, coupang('A'));
    add(`${{folder}}/롯데ON/lotteon_api.txt`, lotte('A', true));
    add(`${{folder}}/Cafe24/cafe24_token.json`, cafe24('A'));

    const ek = V('common.11st.api_key');
    if (ek) {{
      const eleven =
        `api_key=${{ek}}\\r\\n` +
        `seller_id=${{V('common.11st.seller_id')}}\\r\\n` +
        `nickname=${{V('common.11st.nickname')}}\\r\\n` +
        `api_center_url=${{V('common.11st.api_center_url') || 'https://openapi.11st.co.kr/openapi/OpenApiFrontMain.tmall'}}\\r\\n` +
        `registered_pc_ip=${{V('common.11st.registered_pc_ip')}}\\r\\n`;
      add('elevenst_api_key.txt', `API_KEY=${{ek}}\\r\\n`);
      add(`${{folder}}/11번가/11st_upload_id.txt`, eleven);
      add('마켓별_키정리/07_11번가/계정_API정보/11st_upload_id.txt', eleven);
    }}

    if (V('common.openai.api_key')) {{
      add('api_key.txt', V('common.openai.api_key') + '\\r\\n');
      add(`${{folder}}/AI/api_key.txt`, V('common.openai.api_key') + '\\r\\n');
    }}
    if (V('common.anthropic.api_key')) {{
      add('anthropic_api_key.txt', V('common.anthropic.api_key') + '\\r\\n');
      add(`${{folder}}/AI/anthropic_api_key.txt`, V('common.anthropic.api_key') + '\\r\\n');
    }}
    return F;
  }}
  function addServerSaveButton() {{
    const bar = document.querySelector('.bar');
    if (!bar || typeof buildFiles !== 'function') return;
    rewriteForSingleShop();
    window.buildFiles = buildServerFiles;
    const info = document.createElement('div');
    info.style.cssText = 'width:100%;text-align:center;color:#cfd5f0;font-size:12px';
    const count = (window.KEY_MANAGER_CONTEXT.shops || []).length;
    info.textContent = `현재 마켓: ${{window.KEY_MANAGER_CONTEXT.shop_id}} (${{window.KEY_MANAGER_CONTEXT.shop_name}}) · 내 마켓 수: ${{count}}`;
    const btn = document.createElement('button');
    btn.className = 'act btn';
    btn.type = 'button';
    btn.textContent = '서버에 키 저장';
    btn.onclick = async function() {{
      const files = buildFiles();
      if (!files.length) {{
        alert('입력된 항목이 없습니다. 최소 한 마켓이라도 키를 입력하세요.');
        return;
      }}
      btn.disabled = true;
      btn.textContent = '저장 중...';
      try {{
        const res = await fetch('/api/key-files', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{
            name: window.KEY_MANAGER_CONTEXT.name,
            shop_name: window.KEY_MANAGER_CONTEXT.shop_name,
            files
          }})
        }});
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.detail || '저장 실패');
        logLines(files);
        alert(`${{data.shop_id}} 폴더에 ${{data.saved_count}}개 키 파일을 저장했습니다.`);
      }} catch (err) {{
        alert('서버 저장 실패: ' + err.message);
      }} finally {{
        btn.disabled = false;
        btn.textContent = '서버에 키 저장';
      }}
    }};
    bar.insertBefore(info, bar.firstChild);
    bar.insertBefore(btn, bar.firstChild);
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', addServerSaveButton);
  }} else {{
    addServerSaveButton();
  }}
}})();
</script>
"""
    return html.replace("</body>", injection + "\n</body>")


@app.post("/api/key-files")
def api_save_key_files(payload: SaveKeyFilesRequest):
    return save_key_files(payload)


@app.get("/api/shops")
def api_shops(name: str = ""):
    registry = load_shop_registry()
    shops = registry.get("shops", [])
    if name.strip():
        shops = [shop for shop in shops if shop.get("owner_name") == name.strip()]
    return {"ok": True, "shops": shops}


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login():
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>관리자 로그인</title>
  <style>
    body { margin:0; min-height:100vh; display:grid; place-items:center; font-family:Arial,'Malgun Gothic',sans-serif; background:#f5f7fb; color:#202124; }
    main { width:min(420px, calc(100vw - 32px)); background:white; border:1px solid #dde3ee; padding:26px; border-radius:10px; }
    h1 { margin:0 0 18px; font-size:22px; }
    label { display:block; margin-top:14px; font-weight:700; font-size:14px; }
    input { width:100%; box-sizing:border-box; margin-top:6px; padding:11px; font-size:15px; border:1px solid #cfd7e6; border-radius:8px; }
    button { width:100%; margin-top:20px; padding:12px; font-size:15px; font-weight:700; border:0; border-radius:8px; background:#2854c5; color:white; cursor:pointer; }
  </style>
</head>
<body>
  <main>
    <h1>관리자 로그인</h1>
    <form method="post" action="/admin">
      <label>관리자 ID</label>
      <input name="admin_id" required>
      <label>관리자 비밀번호</label>
      <input name="admin_password" type="password" required>
      <button type="submit">회원 승인 관리</button>
    </form>
  </main>
</body>
</html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_get():
    return admin_login()


@app.post("/admin", response_class=HTMLResponse)
def admin_page(admin_id: str = Form(...), admin_password: str = Form(...)):
    require_admin_credentials(admin_id, admin_password)
    rows = list_admin_rows()
    body = "".join(
        f"<tr><td>{row['market_code']}</td><td>{row['name']}</td><td>{row['market_name']}</td>"
        f"<td>{row['user_status']}</td><td>{row['market_status']}</td><td>"
        f"<form method='post' action='/admin/markets/{row['market_id']}/approve' style='display:inline'>"
        f"<input type='hidden' name='admin_id' value='{admin_id}'><input type='hidden' name='admin_password' value='{admin_password}'>"
        f"<button>승인</button></form> "
        f"<form method='post' action='/admin/markets/{row['market_id']}/disable' style='display:inline'>"
        f"<input type='hidden' name='admin_id' value='{admin_id}'><input type='hidden' name='admin_password' value='{admin_password}'>"
        f"<button>비활성</button></form></td></tr>"
        for row in rows
    )
    if not body:
        body = "<tr><td colspan='6' class='empty'>승인할 회원/마켓이 없습니다.</td></tr>"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>회원 승인 관리</title>
  <style>
    body {{ margin:0; font-family:Arial,'Malgun Gothic',sans-serif; background:#f5f7fb; color:#202124; }}
    .wrap {{ max-width:1040px; margin:0 auto; padding:34px 18px; }}
    h1 {{ margin:0 0 18px; font-size:24px; }}
    table {{ width:100%; border-collapse:collapse; background:white; border:1px solid #dde3ee; }}
    th, td {{ padding:11px; border-bottom:1px solid #edf0f5; text-align:left; font-size:14px; }}
    th {{ background:#f0f3f9; }}
    button {{ padding:7px 10px; border:1px solid #cfd7e6; border-radius:7px; background:white; cursor:pointer; }}
    .empty {{ color:#7b8190; text-align:center; padding:28px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>회원 승인 관리</h1>
    <table>
      <thead><tr><th>마켓 ID</th><th>이름</th><th>마켓명</th><th>회원 상태</th><th>마켓 상태</th><th>작업</th></tr></thead>
      <tbody>{body}</tbody>
    </table>
  </div>
</body>
</html>"""


@app.post("/admin/markets/{market_id}/approve", response_class=HTMLResponse)
def admin_approve_market(market_id: int, admin_id: str = Form(...), admin_password: str = Form(...)):
    require_admin_credentials(admin_id, admin_password)
    approve_market(market_id)
    return admin_page(admin_id, admin_password)


@app.post("/admin/markets/{market_id}/disable", response_class=HTMLResponse)
def admin_disable_market(market_id: int, admin_id: str = Form(...), admin_password: str = Form(...)):
    require_admin_credentials(admin_id, admin_password)
    disable_market(market_id)
    return admin_page(admin_id, admin_password)


@app.get("/status")
def status(x_client_token: str | None = Header(None)):
    require_client_token(x_client_token)
    accounts = {}
    for account, meta in COUPANG_ACCOUNTS.items():
        secrets = load_coupang_secrets(account)
        accounts[account] = {
            "label": meta["label"],
            "file": str(meta["path"]),
            "configured": {
                "access_key": bool(secrets["access_key"]),
                "secret_key": bool(secrets["secret_key"]),
                "vendor_id": bool(secrets["vendor_id"]),
                "return_center_code": bool(secrets["return_center_code"]),
                "outbound_shipping_place_code": bool(secrets["outbound_shipping_place_code"]),
            },
        }
    return {
        "ok": True,
        "base_url": COUPANG_BASE_URL,
        "key_root": str(KEY_ROOT),
        "accounts": accounts,
    }


@app.get("/admin/setup", response_class=HTMLResponse)
def setup_form(saved: str = ""):
    home = load_coupang_secrets("home")
    ready = load_coupang_secrets("ready")
    message = "<p class='ok'>저장 완료. 쿠팡 API는 다음 요청부터 새 키를 사용합니다.</p>" if saved else ""
    def fields(account: str, title: str, data: dict[str, str]) -> str:
        return f"""
  <h2>{title}</h2>
  <input type="hidden" name="{account}_enabled" value="1">
  <label>Access Key</label>
  <input name="{account}_access_key" type="password" autocomplete="off" placeholder="기존 값은 다시 표시하지 않습니다">

  <label>Secret Key</label>
  <input name="{account}_secret_key" type="password" autocomplete="off" placeholder="기존 값은 다시 표시하지 않습니다">

  <label>Vendor ID</label>
  <input name="{account}_vendor_id" value="{data['vendor_id']}">

  <label>Vendor User ID</label>
  <input name="{account}_vendor_user_id" value="{data['vendor_user_id']}">

  <label>반품지 코드</label>
  <input name="{account}_return_center_code" value="{data['return_center_code']}">

  <label>출고지 코드</label>
  <input name="{account}_outbound_shipping_place_code" value="{data['outbound_shipping_place_code']}">
"""
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>쿠팡 API 키 설정</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; color: #202124; }}
    label {{ display: block; margin-top: 16px; font-weight: 700; }}
    input {{ width: 100%; box-sizing: border-box; margin-top: 6px; padding: 10px; font-size: 15px; }}
    button {{ margin-top: 20px; padding: 11px 16px; font-size: 15px; cursor: pointer; }}
    h2 {{ margin-top: 28px; padding-top: 18px; border-top: 1px solid #dadce0; font-size: 18px; }}
    .hint {{ color: #5f6368; font-size: 13px; line-height: 1.5; }}
    .ok {{ padding: 10px; background: #e6f4ea; border: 1px solid #b7dfc1; }}
  </style>
</head>
<body>
  <h1>쿠팡 API 키 설정</h1>
  {message}
  <p class="hint">`WEBOCRV2_LOCAL\\키설정도구.html`과 같은 쿠팡 파일 서식으로 서버에 저장합니다. Access/Secret Key는 보안상 기존 값을 다시 보여주지 않습니다. 비워두면 기존 값이 유지됩니다.</p>
  <form method="post" action="/admin/setup">
    <label>관리자 비밀번호</label>
    <input name="admin_password" type="password" autocomplete="current-password" required>

    <label>Coupang Base URL</label>
    <input name="base_url" value="{COUPANG_BASE_URL}" required>

    {fields("home", "홈런 / A 계정 - coupang_wing_api.txt", home)}
    {fields("ready", "준비 / B 계정 - coupang_api_junbi.txt", ready)}

    <button type="submit">서버에 저장</button>
  </form>
</body>
</html>"""


@app.post("/admin/setup")
def save_setup(
    admin_password: str = Form(...),
    base_url: str = Form(...),
    home_access_key: str = Form(""),
    home_secret_key: str = Form(""),
    home_vendor_id: str = Form(""),
    home_vendor_user_id: str = Form(""),
    home_return_center_code: str = Form(""),
    home_outbound_shipping_place_code: str = Form(""),
    ready_access_key: str = Form(""),
    ready_secret_key: str = Form(""),
    ready_vendor_id: str = Form(""),
    ready_vendor_user_id: str = Form(""),
    ready_return_center_code: str = Form(""),
    ready_outbound_shipping_place_code: str = Form(""),
):
    require_admin_password(admin_password)
    global COUPANG_BASE_URL
    COUPANG_BASE_URL = base_url.strip() or "https://api-gateway.coupang.com"

    def merge_and_save(account: str, prefix: str, values: dict[str, str]) -> None:
        current = load_coupang_secrets(account)
        current["url"] = COUPANG_BASE_URL
        for key, value in values.items():
            if key in {"access_key", "secret_key"} and not value.strip():
                continue
            current[key] = value.strip()
        save_coupang_secrets(account, current)

    merge_and_save("home", "home", {
        "access_key": home_access_key,
        "secret_key": home_secret_key,
        "vendor_id": home_vendor_id,
        "vendor_user_id": home_vendor_user_id,
        "return_center_code": home_return_center_code,
        "outbound_shipping_place_code": home_outbound_shipping_place_code,
    })
    merge_and_save("ready", "ready", {
        "access_key": ready_access_key,
        "secret_key": ready_secret_key,
        "vendor_id": ready_vendor_id,
        "vendor_user_id": ready_vendor_user_id,
        "return_center_code": ready_return_center_code,
        "outbound_shipping_place_code": ready_outbound_shipping_place_code,
    })
    return RedirectResponse("/admin/setup?saved=1", status_code=303)


@app.post("/coupang/predict-category")
async def predict_category(
    body: ProductNameRequest,
    x_client_token: str | None = Header(None),
):
    require_client_token(x_client_token)
    return await coupang_request(
        "POST",
        "/v2/providers/openapi/apis/api/v1/categorization/predict",
        account=body.account,
        body={"productName": body.product_name},
    )


@app.post("/coupang/category-meta")
async def category_meta(
    body: CategoryMetaRequest,
    x_client_token: str | None = Header(None),
):
    require_client_token(x_client_token)
    path = (
        "/v2/providers/seller_api/apis/api/v1/marketplace/meta/"
        f"category-related-metas/display-category-codes/{body.category_code}"
    )
    return await coupang_request("GET", path, account=body.account)


@app.post("/coupang/proxy")
async def coupang_proxy(
    body: CoupangProxyRequest,
    x_client_token: str | None = Header(None),
):
    require_client_token(x_client_token)
    return await coupang_request(body.method, body.path, body.account, body.query, body.body)
