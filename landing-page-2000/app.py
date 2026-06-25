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
import hmac
import json
import os
import re
import secrets
import sqlite3
import base64
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import bcrypt
import httpx
from cryptography.fernet import Fernet, InvalidToken

DB_PATH = os.environ.get("PORTAL_DB", "/data/portal.db")
if not os.path.isdir(os.path.dirname(DB_PATH) or "."):
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "portal.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

ADMIN_EMAIL = (os.environ.get("PORTAL_ADMIN_EMAIL") or "").strip().lower()
ADMIN_PASSWORD = os.environ.get("PORTAL_ADMIN_PASSWORD") or ""
KEY_SECRET = os.environ.get("PORTAL_KEY_SECRET") or ADMIN_PASSWORD or "dev-only-change-me"
HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="RK Work Portal", version="1.0.0")

ALLOWED_MARKETS = {"naver", "coupang", "cafe24", "lotteon", "11st", "esm", "other"}
MARKET_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
COUPANG_BASE_URL = os.environ.get("COUPANG_BASE_URL", "https://api-gateway.coupang.com")
NAVER_COMMERCE_BASE_URL = os.environ.get("NAVER_COMMERCE_BASE_URL", "https://api.commerce.naver.com/external")


def key_cipher() -> Fernet:
    digest = hashlib.sha256(KEY_SECRET.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return key_cipher().encrypt(raw).decode("ascii")


def decrypt_payload(token: str) -> dict[str, Any]:
    try:
        raw = key_cipher().decrypt(token.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (InvalidToken, ValueError, TypeError):
        return {}


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
        conn.execute(
            """CREATE TABLE IF NOT EXISTS market_keys(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                market_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                key_payload_encrypted TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'saved',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, market_id, market)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cafeshipment_orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                market_id INTEGER NOT NULL,
                source_market TEXT NOT NULL,
                mall_id TEXT DEFAULT '',
                market_name TEXT DEFAULT '',
                order_id TEXT NOT NULL,
                order_item_code TEXT DEFAULT '',
                recipient_phone TEXT DEFAULT '',
                recipient_name TEXT DEFAULT '',
                recipient_cell_phone TEXT DEFAULT '',
                order_status TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                product_code TEXT DEFAULT '',
                order_amount REAL DEFAULT 0,
                quantity INTEGER DEFAULT 0,
                order_date TEXT DEFAULT '',
                shipping_code TEXT DEFAULT '',
                raw_json TEXT DEFAULT '',
                collected_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, market_id, source_market, order_id, order_item_code)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cafeshipment_product_code_overrides(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                product_code TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, order_id)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cafeship_orders_user_market ON cafeshipment_orders(user_id, market_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cafeship_orders_phone ON cafeshipment_orders(recipient_cell_phone, recipient_phone)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cafeship_orders_date ON cafeshipment_orders(order_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cafeship_code_overrides_order ON cafeshipment_product_code_overrides(order_id)")
        order_cols = [r[1] for r in conn.execute("PRAGMA table_info(cafeshipment_orders)").fetchall()]
        if "product_code" not in order_cols:
            conn.execute("ALTER TABLE cafeshipment_orders ADD COLUMN product_code TEXT DEFAULT ''")
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
def request_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.cookies.get("sid")


def current_user(request: Request) -> sqlite3.Row | None:
    token = request_token(request)
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


class KeySaveBody(BaseModel):
    market_id: int
    market: str
    payload: dict[str, Any]


class MarketProxyBody(BaseModel):
    market_id: int
    method: str = "GET"
    path: str
    query: dict[str, Any] = {}
    body: dict[str, Any] | list[Any] | None = None


class CafeShipmentCollectBody(BaseModel):
    market_id: int
    start_date: str | None = None
    end_date: str | None = None
    order_status: str | None = None


class CafeShipmentExportBody(BaseModel):
    order_ids: list[int]
    market_name: str = "홈런마켓"
    export_date: str | None = None


class CafeShipmentProductCodeBody(BaseModel):
    order_id: int
    product_code: str
    note: str = ""


def require_active_user(request: Request) -> sqlite3.Row:
    u = require_user(request)
    if not (u["status"] == "active" or u["is_admin"]):
        raise HTTPException(status_code=403, detail="관리자 승인 후 사용할 수 있습니다.")
    return u


def require_owned_market(conn: sqlite3.Connection, user: sqlite3.Row, market_id: int) -> sqlite3.Row:
    m = conn.execute("SELECT * FROM markets WHERE id=?", (market_id,)).fetchone()
    if not m or (m["user_id"] != user["id"] and not user["is_admin"]):
        raise HTTPException(status_code=404, detail="마켓을 찾을 수 없습니다.")
    return m


def clean_key_payload(payload: dict[str, Any]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in payload.items():
        k = str(key).strip()
        if not k or len(k) > 80:
            continue
        text = "" if value is None else str(value).strip()
        if len(text) > 10000:
            raise HTTPException(status_code=400, detail=f"{k} 값이 너무 깁니다.")
        clean[k] = text
    if not any(v for v in clean.values()):
        raise HTTPException(status_code=400, detail="저장할 키 값을 입력하세요.")
    return clean


def key_status(row: sqlite3.Row) -> dict[str, Any]:
    payload = decrypt_payload(row["key_payload_encrypted"])
    fields = sorted([k for k, v in payload.items() if str(v).strip()])
    return {
        "id": row["id"],
        "marketId": row["market_id"],
        "market": row["market"],
        "status": row["status"],
        "saved": bool(fields),
        "fields": fields,
        "updatedAt": row["updated_at"],
    }


def validate_market_path(path: str, allowed_prefixes: tuple[str, ...], market_name: str) -> None:
    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail=f"{market_name} API path는 / 로 시작해야 합니다.")
    if "://" in path or ".." in path or "\\" in path:
        raise HTTPException(status_code=400, detail=f"{market_name} API path가 올바르지 않습니다.")
    if not any(path.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(status_code=400, detail=f"{market_name} API path는 아직 허용되지 않은 경로입니다.")


def normalize_method(method: str) -> str:
    normalized = method.strip().upper()
    if normalized not in MARKET_METHODS:
        raise HTTPException(status_code=400, detail="지원하지 않는 HTTP method입니다.")
    return normalized


def get_market_key_payload(conn: sqlite3.Connection, user: sqlite3.Row, market_id: int, market: str) -> dict[str, str]:
    require_owned_market(conn, user, market_id)
    row = conn.execute(
        "SELECT key_payload_encrypted FROM market_keys WHERE user_id=? AND market_id=? AND market=?",
        (user["id"], market_id, market),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="저장된 마켓 키가 없습니다.")
    payload = decrypt_payload(row["key_payload_encrypted"])
    if not payload:
        raise HTTPException(status_code=500, detail="저장된 마켓 키를 복호화할 수 없습니다.")
    return {str(k): "" if v is None else str(v).strip() for k, v in payload.items()}


def require_fields(payload: dict[str, str], names: list[str], market_name: str) -> None:
    missing = [name for name in names if not payload.get(name)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"message": f"{market_name} API 호출에 필요한 키가 부족합니다.", "missing": missing},
        )


def market_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data: Any = response.json()
    except ValueError:
        text = response.text
        data = text[:2000] if len(text) > 2000 else text
    return {"ok": response.is_success, "statusCode": response.status_code, "data": data}


def coupang_authorization(method: str, path: str, query: dict[str, Any], access_key: str, secret_key: str) -> str:
    signed_date = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    query_string = urlencode(query, doseq=True)
    message = f"{signed_date}{method}{path}{query_string}"
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        "CEA algorithm=HmacSHA256, "
        f"access-key={access_key}, "
        f"signed-date={signed_date}, "
        f"signature={signature}"
    )


async def call_coupang_api(method: str, path: str, query: dict[str, Any], body: Any, keys: dict[str, str]) -> dict[str, Any]:
    require_fields(keys, ["access_key", "secret_key", "vendor_id"], "쿠팡")
    validate_market_path(
        path,
        (
            "/v2/providers/openapi/apis/api/",
            "/v2/providers/seller_api/apis/api/",
            "/v2/providers/marketplace_openapi/apis/api/",
        ),
        "쿠팡",
    )
    headers = {
        "Authorization": coupang_authorization(method, path, query, keys["access_key"], keys["secret_key"]),
        "Content-Type": "application/json;charset=UTF-8",
        "Accept-Encoding": "gzip, identity",
        "X-EXTENDED-TIMEOUT": "90000",
        "X-Requested-By": keys["vendor_id"],
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.request(
            method,
            f"{COUPANG_BASE_URL}{path}",
            params=query,
            json=body if method != "GET" else None,
            headers=headers,
        )
    return market_response(response)


async def naver_access_token(keys: dict[str, str]) -> str:
    require_fields(keys, ["NAVER_COMMERCE_CLIENT_ID", "NAVER_COMMERCE_CLIENT_SECRET"], "네이버")
    timestamp = str(int(time.time() * 1000) - 3000)
    client_id = keys["NAVER_COMMERCE_CLIENT_ID"]
    client_secret = keys["NAVER_COMMERCE_CLIENT_SECRET"]
    password = f"{client_id}_{timestamp}".encode("utf-8")
    try:
        hashed = bcrypt.hashpw(password, client_secret.encode("utf-8"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="네이버 Client Secret 형식이 올바르지 않습니다.") from exc
    sign = base64.b64encode(hashed).decode("utf-8")
    data = {
        "client_id": client_id,
        "timestamp": timestamp,
        "client_secret_sign": sign,
        "grant_type": "client_credentials",
        "type": "SELF",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{NAVER_COMMERCE_BASE_URL}/v1/oauth2/token", data=data)
    if not response.is_success:
        return ""
    token = response.json().get("access_token")
    return str(token or "")


async def call_naver_api(method: str, path: str, query: dict[str, Any], body: Any, keys: dict[str, str]) -> dict[str, Any]:
    validate_market_path(path, ("/v1/",), "네이버")
    token = await naver_access_token(keys)
    if not token:
        raise HTTPException(status_code=502, detail="네이버 액세스 토큰 발급에 실패했습니다.")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json;charset=UTF-8"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.request(
            method,
            f"{NAVER_COMMERCE_BASE_URL}{path}",
            params=query,
            json=body if method != "GET" else None,
            headers=headers,
        )
    return market_response(response)


async def call_cafe24_api(method: str, path: str, query: dict[str, Any], body: Any, keys: dict[str, str]) -> dict[str, Any]:
    require_fields(keys, ["MallId", "AccessToken"], "Cafe24")
    validate_market_path(path, ("/api/v2/admin/",), "Cafe24")
    api_version = keys.get("ApiVersion") or "2025-12-01"
    headers = {
        "Authorization": f"Bearer {keys['AccessToken']}",
        "X-Cafe24-Api-Version": api_version,
        "Content-Type": "application/json;charset=UTF-8",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.request(
            method,
            f"https://{keys['MallId']}.cafe24api.com{path}",
            params=query,
            json=body if method != "GET" else None,
            headers=headers,
        )
    return market_response(response)


async def call_market_api(market: str, method: str, path: str, query: dict[str, Any], body: Any, keys: dict[str, str]) -> dict[str, Any]:
    if market == "coupang":
        return await call_coupang_api(method, path, query, body, keys)
    if market == "naver":
        return await call_naver_api(method, path, query, body, keys)
    if market == "cafe24":
        return await call_cafe24_api(method, path, query, body, keys)
    raise HTTPException(status_code=400, detail=f"{market} API 호출은 아직 구현되지 않았습니다.")


def normalize_phone(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def parse_iso_date(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise HTTPException(status_code=400, detail=f"날짜 형식이 올바르지 않습니다: {value}")


def compact_product_text(value: str | None) -> str:
    text = value or ""
    for token in ("일시품절[삭제]", "일시품절", "[삭제]"):
        text = text.replace(token, "")
    return " ".join(text.split())


def pick_text(data: dict[str, Any], *names: str) -> str:
    for name in names:
        value = data.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def cafe24_receiver(order: dict[str, Any]) -> dict[str, Any]:
    receivers = order.get("receivers")
    if isinstance(receivers, list) and receivers and isinstance(receivers[0], dict):
        return receivers[0]
    return {}


def parse_cafe24_order(order: dict[str, Any], item: dict[str, Any] | None, keys: dict[str, str], market_id: int, market_name: str) -> dict[str, Any]:
    receiver = cafe24_receiver(order)
    order_id = pick_text(order, "order_id")
    receiver_name = (
        pick_text(receiver, "name", "receiver_name")
        or pick_text(order, "receiver_name", "buyer_name", "billing_name")
    )
    receiver_cell = (
        pick_text(receiver, "cellphone", "receiver_cellphone")
        or pick_text(order, "receiver_cellphone", "buyer_cellphone")
    )
    receiver_phone = (
        pick_text(receiver, "phone", "receiver_phone")
        or pick_text(order, "receiver_phone", "buyer_phone")
    )
    item_data = item or {}
    amount_raw = pick_text(item_data, "product_price")
    qty_raw = pick_text(item_data, "quantity")
    try:
        amount = float(amount_raw) if amount_raw else 0
    except ValueError:
        amount = 0
    try:
        quantity = int(float(qty_raw)) if qty_raw else 0
    except ValueError:
        quantity = 0
    return {
        "market_id": market_id,
        "source_market": "cafe24",
        "mall_id": keys.get("MallId", ""),
        "market_name": market_name,
        "order_id": order_id,
        "order_item_code": pick_text(item_data, "order_item_code"),
        "recipient_phone": normalize_phone(receiver_phone),
        "recipient_name": receiver_name,
        "recipient_cell_phone": normalize_phone(receiver_cell),
        "order_status": pick_text(item_data, "order_status") or pick_text(order, "order_status"),
        "product_name": compact_product_text(pick_text(item_data, "product_name")),
        "product_code": pick_text(item_data, "custom_product_code", "product_code", "variant_code", "supplier_product_code"),
        "order_amount": amount,
        "quantity": quantity,
        "order_date": pick_text(order, "order_date"),
        "shipping_code": pick_text(item_data, "shipping_code"),
        "raw_json": json.dumps(order, ensure_ascii=False),
    }


def kst_iso_offset(dt: datetime, end_of_day: bool = False) -> str:
    return f"{dt.date():%Y-%m-%d}+09:00"


def parse_coupang_order_sheet(order_sheet: dict[str, Any], keys: dict[str, str], market_id: int, market_name: str) -> list[dict[str, Any]]:
    receiver = order_sheet.get("receiver") if isinstance(order_sheet.get("receiver"), dict) else {}
    items = order_sheet.get("orderItems")
    if not isinstance(items, list):
        return []
    order_id = pick_text(order_sheet, "orderId")
    ordered_at = pick_text(order_sheet, "orderedAt")
    status = pick_text(order_sheet, "status")
    recipient_name = pick_text(receiver, "name")
    safe_number = pick_text(receiver, "safeNumber") or pick_text(receiver, "receiverNumber")
    receiver_number = pick_text(receiver, "receiverNumber")
    shipment_box_id = pick_text(order_sheet, "shipmentBoxId")
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        product_parts = [
            pick_text(item, "sellerProductName"),
            pick_text(item, "sellerProductItemName"),
            pick_text(item, "vendorItemName"),
        ]
        product_name = " / ".join(dict.fromkeys([p for p in product_parts if p]))
        qty_raw = pick_text(item, "shippingCount") or pick_text(item, "quantity")
        price_raw = pick_text(item, "orderPrice") or pick_text(item, "salesPrice")
        try:
            quantity = int(float(qty_raw)) if qty_raw else 0
        except ValueError:
            quantity = 0
        try:
            amount = float(price_raw) if price_raw else 0
        except ValueError:
            amount = 0
        rows.append({
            "market_id": market_id,
            "source_market": "coupang",
            "mall_id": keys.get("vendor_id", ""),
            "market_name": market_name,
            "order_id": order_id,
            "order_item_code": pick_text(item, "vendorItemId"),
            "recipient_phone": normalize_phone(receiver_number),
            "recipient_name": recipient_name,
            "recipient_cell_phone": normalize_phone(safe_number),
            "order_status": status,
            "product_name": compact_product_text(product_name),
            "product_code": pick_text(item, "externalVendorSkuCode", "sellerProductId", "sellerProductItemId", "vendorItemId"),
            "order_amount": amount,
            "quantity": quantity,
            "order_date": ordered_at,
            "shipping_code": pick_text(item, "shipmentBoxId") or shipment_box_id,
            "raw_json": json.dumps(order_sheet, ensure_ascii=False),
        })
    return rows


def response_data_list(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    data = result.get("data")
    if isinstance(data, dict):
        values = data.get(key)
        if isinstance(values, list):
            return [v for v in values if isinstance(v, dict)]
    return []


async def collect_cafe24_orders(keys: dict[str, str], market_id: int, market_name: str, start_dt: datetime, end_dt: datetime, order_status: str | None) -> list[dict[str, Any]]:
    require_fields(keys, ["MallId", "AccessToken"], "Cafe24")
    orders: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    requested_statuses = [s.strip() for s in (order_status or "").split(",") if s.strip()]
    while True:
        query: dict[str, Any] = {
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": end_dt.strftime("%Y-%m-%d"),
            "limit": limit,
            "offset": offset,
            "embed": "receivers,items",
        }
        if requested_statuses:
            query["order_status"] = ",".join(requested_statuses)
        result = await call_cafe24_api("GET", "/api/v2/admin/orders", query, None, keys)
        if not result["ok"]:
            raise HTTPException(status_code=502, detail={"message": "Cafe24 주문 조회 실패", "statusCode": result["statusCode"], "data": result["data"]})
        page_orders = response_data_list(result, "orders")
        if not page_orders:
            break
        for order in page_orders:
            order_id = pick_text(order, "order_id")
            items = order.get("items")
            if isinstance(items, list) and items:
                matched = 0
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_status = pick_text(item, "order_status")
                    if requested_statuses and item_status not in requested_statuses:
                        continue
                    row = parse_cafe24_order(order, item, keys, market_id, market_name)
                    if row["order_id"] or row["order_item_code"]:
                        orders.append(row)
                        matched += 1
                if matched == 0 and not requested_statuses:
                    row = parse_cafe24_order(order, None, keys, market_id, market_name)
                    if row["order_id"] or order_id:
                        orders.append(row)
            else:
                status = pick_text(order, "order_status")
                if requested_statuses and status not in requested_statuses:
                    continue
                row = parse_cafe24_order(order, None, keys, market_id, market_name)
                if row["order_id"] or order_id:
                    orders.append(row)
        if len(page_orders) < limit:
            break
        offset += limit
    return orders


async def collect_coupang_orders(keys: dict[str, str], market_id: int, market_name: str, start_dt: datetime, end_dt: datetime, order_status: str | None) -> list[dict[str, Any]]:
    require_fields(keys, ["access_key", "secret_key", "vendor_id"], "쿠팡")
    statuses = [s.strip().upper() for s in (order_status or "").split(",") if s.strip()]
    if not statuses:
        statuses = ["ACCEPT", "INSTRUCT"]
    orders: list[dict[str, Any]] = []
    for status in statuses:
        next_token = ""
        while True:
            query: dict[str, Any] = {
                "createdAtFrom": kst_iso_offset(start_dt),
                "createdAtTo": kst_iso_offset(end_dt, end_of_day=True),
                "status": status,
                "maxPerPage": "50",
            }
            if next_token:
                query["nextToken"] = next_token
            path = f"/v2/providers/openapi/apis/api/v5/vendors/{keys['vendor_id']}/ordersheets"
            result = await call_coupang_api("GET", path, query, None, keys)
            if not result["ok"]:
                raise HTTPException(status_code=502, detail={"message": "쿠팡 주문 조회 실패", "statusCode": result["statusCode"], "data": result["data"]})
            data = result.get("data")
            if not isinstance(data, dict):
                break
            page_orders = data.get("data")
            if not isinstance(page_orders, list) or not page_orders:
                break
            for order_sheet in page_orders:
                if isinstance(order_sheet, dict):
                    orders.extend(parse_coupang_order_sheet(order_sheet, keys, market_id, market_name))
            next_token = str(data.get("nextToken") or "").strip()
            if not next_token:
                break
    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in orders:
        dedup[(row["mall_id"], row["order_id"], row["order_item_code"])] = row
    return list(dedup.values())


def upsert_cafeshipment_orders(conn: sqlite3.Connection, user_id: int, rows: list[dict[str, Any]]) -> int:
    ts = now()
    for row in rows:
        conn.execute(
            """INSERT INTO cafeshipment_orders(
                   user_id,market_id,source_market,mall_id,market_name,order_id,order_item_code,
                   recipient_phone,recipient_name,recipient_cell_phone,order_status,product_name,
                   product_code,order_amount,quantity,order_date,shipping_code,raw_json,collected_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,market_id,source_market,order_id,order_item_code) DO UPDATE SET
                   mall_id=excluded.mall_id,
                   market_name=excluded.market_name,
                   recipient_phone=excluded.recipient_phone,
                   recipient_name=excluded.recipient_name,
                   recipient_cell_phone=excluded.recipient_cell_phone,
                   order_status=excluded.order_status,
                   product_name=excluded.product_name,
                   product_code=excluded.product_code,
                   order_amount=excluded.order_amount,
                   quantity=excluded.quantity,
                   order_date=excluded.order_date,
                   shipping_code=excluded.shipping_code,
                   raw_json=excluded.raw_json,
                   collected_at=excluded.collected_at,
                   updated_at=excluded.updated_at""",
            (
                user_id,
                row["market_id"],
                row["source_market"],
                row["mall_id"],
                row["market_name"],
                row["order_id"],
                row["order_item_code"],
                row["recipient_phone"],
                row["recipient_name"],
                row["recipient_cell_phone"],
                row["order_status"],
                row["product_name"],
                row.get("product_code", ""),
                row["order_amount"],
                row["quantity"],
                row["order_date"],
                row["shipping_code"],
                row["raw_json"],
                ts,
                ts,
            ),
        )
    conn.commit()
    return len(rows)


def infer_product_code_from_raw(row: sqlite3.Row) -> str:
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except (TypeError, ValueError):
        return ""
    source = str(row["source_market"] or "").lower()
    order_item_code = str(row["order_item_code"] or "")
    if source == "coupang":
        items = raw.get("orderItems") if isinstance(raw, dict) else None
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                if order_item_code and pick_text(item, "vendorItemId") != order_item_code:
                    continue
                return pick_text(item, "externalVendorSkuCode", "sellerProductId", "sellerProductItemId", "vendorItemId")
    if source == "cafe24":
        items = raw.get("items") if isinstance(raw, dict) else None
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                if order_item_code and pick_text(item, "order_item_code") != order_item_code:
                    continue
                return pick_text(item, "custom_product_code", "product_code", "variant_code", "supplier_product_code")
    return ""


def clean_manual_product_code(value: str) -> str:
    code = clean_export_value(value).upper()
    if len(code) > 80:
        raise HTTPException(status_code=400, detail="상품코드는 80자 이내로 입력하세요.")
    return code


def clean_override_note(value: str) -> str:
    note = clean_export_value(value)
    if len(note) > 500:
        raise HTTPException(status_code=400, detail="메모는 500자 이내로 입력하세요.")
    return note


def row_value(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    return row[key] if key in row.keys() else default


PRODUCT_CODE_RE = re.compile(r"\b([A-Z]{2,}\d+[A-Z])\b", re.IGNORECASE)
OPTION_LETTER_RE = re.compile(r"=\s*([A-Za-z])")


def clean_export_value(value: Any) -> str:
    return compact_product_text(str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ").strip())


def extract_product_code(text: str | None) -> str:
    normalized = compact_product_text(text or "")
    if not normalized:
        return ""
    match = PRODUCT_CODE_RE.search(normalized)
    return match.group(1).upper() if match else ""


def extract_option_letter(option_text: str | None) -> str:
    if not option_text:
        return ""
    match = OPTION_LETTER_RE.search(option_text)
    return match.group(1).upper() if match else ""


def apply_option_letter(base_product_code: str, option_text: str | None) -> str:
    if not base_product_code:
        return ""
    normalized = base_product_code.strip().upper()
    replacement = extract_option_letter(option_text)
    if replacement and normalized and normalized[-1].isalpha():
        normalized = normalized[:-1] + replacement
    return normalized


def parse_raw_json(raw_json: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_json or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def combine_address(address1: str, detail_address: str, address_full: str) -> str:
    first = clean_export_value(address1)
    second = clean_export_value(detail_address)
    if first and second:
        return f"{first} {second}"
    return first or second or clean_export_value(address_full)


def select_cafe24_receiver(order_json: dict[str, Any], shipping_code: str) -> dict[str, Any]:
    receivers = order_json.get("receivers")
    if not isinstance(receivers, list):
        return {}
    for receiver in receivers:
        if isinstance(receiver, dict) and shipping_code and pick_text(receiver, "shipping_code") == shipping_code:
            return receiver
    return next((r for r in receivers if isinstance(r, dict)), {})


def select_cafe24_item(order_json: dict[str, Any], order_item_code: str) -> dict[str, Any]:
    items = order_json.get("items")
    if not isinstance(items, list):
        return {}
    for item in items:
        if isinstance(item, dict) and order_item_code and pick_text(item, "order_item_code") == order_item_code:
            return item
    return next((i for i in items if isinstance(i, dict)), {})


def resolve_cafe24_option_text(item: dict[str, Any]) -> str:
    direct = compact_product_text(pick_text(item, "option_value", "option_value_default"))
    if direct:
        return direct
    options = item.get("options")
    if not isinstance(options, list):
        return ""
    parts: list[str] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        name = pick_text(option, "option_name")
        raw_value = option.get("option_value")
        if isinstance(raw_value, dict):
            text = compact_product_text(pick_text(raw_value, "option_text"))
        else:
            text = compact_product_text(str(raw_value or ""))
        if name and text:
            parts.append(f"{name}={text}")
        elif name:
            parts.append(name)
        elif text:
            parts.append(text)
    return " / ".join(parts)


def resolve_cafe24_export_product_code(item: dict[str, Any], row: sqlite3.Row) -> str:
    custom = pick_text(item, "custom_product_code")
    if custom:
        return custom.upper()
    for candidate in (
        pick_text(item, "supplier_product_name"),
        pick_text(item, "product_name"),
        row["product_name"],
    ):
        extracted = extract_product_code(candidate)
        if extracted:
            return extracted
    return ""


def select_coupang_item(order_json: dict[str, Any], order_item_code: str, shipping_code: str) -> dict[str, Any]:
    items = order_json.get("orderItems")
    if not isinstance(items, list):
        return {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if (order_item_code and pick_text(item, "vendorItemId") == order_item_code) or (
            shipping_code and pick_text(item, "shipmentBoxId") == shipping_code
        ):
            return item
    return next((i for i in items if isinstance(i, dict)), {})


def resolve_coupang_export_product_code(item: dict[str, Any], row: sqlite3.Row) -> str:
    for candidate in (
        pick_text(item, "externalVendorSkuCode"),
        pick_text(item, "sellerProductItemName"),
        pick_text(item, "sellerProductName"),
        pick_text(item, "vendorItemName"),
        row["product_name"],
    ):
        extracted = extract_product_code(candidate)
        if extracted:
            return extracted
    return ""


def shipment_export_row(row: sqlite3.Row, market_name: str, export_date: str) -> list[str]:
    return shipment_export_detail(row, market_name, export_date)["values"]


def shipment_export_detail(row: sqlite3.Row, market_name: str, export_date: str) -> dict[str, Any]:
    manual_product_code = clean_manual_product_code(str(row_value(row, "manual_product_code", "") or ""))
    raw = parse_raw_json(row["raw_json"])
    source = str(row["source_market"] or "").lower()
    option_text = ""
    base_product_code = ""
    product_code = ""
    if source == "coupang":
        receiver = raw.get("receiver") if isinstance(raw.get("receiver"), dict) else {}
        item = select_coupang_item(raw, row["order_item_code"], row["shipping_code"])
        option_text = pick_text(item, "sellerProductItemName") or pick_text(item, "vendorItemName")
        base_product_code = resolve_coupang_export_product_code(item, row)
        product_code = manual_product_code or apply_option_letter(base_product_code, option_text)
        detail_address = pick_text(receiver, "addr2")
        full_address = combine_address(
            pick_text(receiver, "addr1"),
            detail_address,
            " ".join(v for v in (pick_text(receiver, "addr1"), pick_text(receiver, "addr2")) if v),
        )
        values = [
            product_code,
            market_name,
            export_date,
            str(row["quantity"] or 0),
            pick_text(receiver, "name") or row["recipient_name"],
            pick_text(receiver, "safeNumber", "receiverNumber") or row["recipient_cell_phone"] or row["recipient_phone"],
            pick_text(receiver, "postCode"),
            full_address,
            pick_text(raw, "parcelPrintMessage", "deliveryInstruction"),
            detail_address,
        ]
        return shipment_export_detail_payload(row, values, option_text, base_product_code, manual_product_code)

    receiver = select_cafe24_receiver(raw, row["shipping_code"])
    item = select_cafe24_item(raw, row["order_item_code"])
    option_text = resolve_cafe24_option_text(item)
    base_product_code = resolve_cafe24_export_product_code(item, row)
    product_code = manual_product_code or apply_option_letter(base_product_code, option_text)
    detail_address = pick_text(receiver, "address2")
    full_address = combine_address(
        pick_text(receiver, "address1"),
        detail_address,
        pick_text(receiver, "address_full"),
    )
    values = [
        product_code,
        market_name,
        export_date,
        str(row["quantity"] or 0),
        pick_text(receiver, "name") or row["recipient_name"],
        pick_text(receiver, "cellphone", "phone") or row["recipient_cell_phone"] or row["recipient_phone"],
        pick_text(receiver, "zipcode"),
        full_address,
        pick_text(receiver, "shipping_message"),
        detail_address,
    ]
    return shipment_export_detail_payload(row, values, option_text, base_product_code, manual_product_code)


def shipment_export_detail_payload(
    row: sqlite3.Row,
    values: list[str],
    option_text: str,
    base_product_code: str,
    manual_product_code: str,
) -> dict[str, Any]:
    return {
        "orderId": row["id"],
        "sourceMarket": row["source_market"],
        "marketName": row["market_name"],
        "productName": row["product_name"],
        "optionText": option_text,
        "baseProductCode": base_product_code,
        "manualProductCode": manual_product_code,
        "productCode": values[0],
        "quantity": row["quantity"] or 0,
        "recipientName": row["recipient_name"],
        "values": values,
    }


def build_shipment_clipboard(rows: list[sqlite3.Row], market_name: str, export_date: str) -> tuple[str, int]:
    built = [shipment_export_detail(row, market_name, export_date) for row in rows]
    built = [row for row in built if row.get("values")]
    blank = [row for row in built if not row["values"][0].strip()]
    normal = [row for row in built if row["values"][0].strip()]
    ordered = blank + normal
    text = "\n".join("\t".join(clean_export_value(value) for value in row["values"]) for row in ordered)
    return text, len(blank)


def build_shipment_export_preview(rows: list[sqlite3.Row], market_name: str, export_date: str) -> list[dict[str, Any]]:
    built = [shipment_export_detail(row, market_name, export_date) for row in rows]
    blank = [row for row in built if not str(row.get("productCode") or "").strip()]
    normal = [row for row in built if str(row.get("productCode") or "").strip()]
    return blank + normal


def cafeshipment_order_public(row: sqlite3.Row) -> dict[str, Any]:
    product_code = shipment_export_row(
        row,
        row["market_name"] or "홈런마켓",
        (row["order_date"] or "")[:10],
    )[0]
    return {
        "id": row["id"],
        "marketId": row["market_id"],
        "sourceMarket": row["source_market"],
        "mallId": row["mall_id"],
        "marketName": row["market_name"],
        "orderId": row["order_id"],
        "orderItemCode": row["order_item_code"],
        "recipientPhone": row["recipient_phone"],
        "recipientName": row["recipient_name"],
        "recipientCellPhone": row["recipient_cell_phone"],
        "orderStatus": row["order_status"],
        "productName": row["product_name"],
        "productCode": product_code,
        "manualProductCode": row_value(row, "manual_product_code", ""),
        "orderAmount": row["order_amount"],
        "quantity": row["quantity"],
        "orderDate": row["order_date"],
        "shippingCode": row["shipping_code"],
        "collectedAt": row["collected_at"],
        "updatedAt": row["updated_at"],
    }


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
    return {"ok": True, "token": token, "user": user_public(u)}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request_token(request)
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
        conn.execute("DELETE FROM market_keys WHERE market_id=? AND user_id=?", (market_id, u["id"]))
        conn.execute("DELETE FROM markets WHERE id=?", (market_id,))
        conn.commit()
    return {"ok": True}


# ── 마켓별 키 저장 ──
@app.get("/api/keys")
def list_keys(request: Request, market_id: int | None = None):
    u = require_user(request)
    q = "SELECT * FROM market_keys WHERE user_id=?"
    params: list[Any] = [u["id"]]
    if market_id is not None:
        q += " AND market_id=?"
        params.append(market_id)
    q += " ORDER BY updated_at DESC"
    with db() as conn:
        rows = conn.execute(q, params).fetchall()
    return {"ok": True, "keys": [key_status(row) for row in rows]}


@app.post("/api/keys")
def save_keys(body: KeySaveBody, request: Request):
    u = require_active_user(request)
    market = body.market.strip().lower()
    if market not in ALLOWED_MARKETS:
        raise HTTPException(status_code=400, detail="지원하지 않는 마켓입니다.")
    payload = clean_key_payload(body.payload)
    encrypted = encrypt_payload(payload)
    ts = now()
    with db() as conn:
        require_owned_market(conn, u, body.market_id)
        existing = conn.execute(
            "SELECT id FROM market_keys WHERE user_id=? AND market_id=? AND market=?",
            (u["id"], body.market_id, market),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE market_keys
                   SET key_payload_encrypted=?, status='saved', updated_at=?
                   WHERE id=?""",
                (encrypted, ts, existing["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO market_keys(user_id,market_id,market,key_payload_encrypted,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (u["id"], body.market_id, market, encrypted, "saved", ts, ts),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM market_keys WHERE user_id=? AND market_id=? AND market=?",
            (u["id"], body.market_id, market),
        ).fetchone()
    return {"ok": True, "key": key_status(row)}


# ── 마켓 API 호출 대행 ──
@app.post("/api/market/{market}/proxy")
async def market_proxy(market: str, body: MarketProxyBody, request: Request):
    u = require_active_user(request)
    market = market.strip().lower()
    if market not in ALLOWED_MARKETS:
        raise HTTPException(status_code=400, detail="지원하지 않는 마켓입니다.")
    method = normalize_method(body.method)
    query = {str(k): v for k, v in (body.query or {}).items() if str(k).strip()}
    with db() as conn:
        keys = get_market_key_payload(conn, u, body.market_id, market)
    result = await call_market_api(market, method, body.path.strip(), query, body.body, keys)
    return {
        "ok": result["ok"],
        "market": market,
        "marketId": body.market_id,
        "statusCode": result["statusCode"],
        "data": result["data"],
    }


# ── CafeShipment: 주문 수집/조회 ──
@app.post("/api/cafeshipment/orders/collect")
async def cafeshipment_collect_orders(body: CafeShipmentCollectBody, request: Request):
    u = require_active_user(request)
    default_end = datetime.now(timezone.utc)
    default_start = default_end - timedelta(days=14)
    start_dt = parse_iso_date(body.start_date, default_start)
    end_dt = parse_iso_date(body.end_date, default_end)
    if start_dt.date() > end_dt.date():
        raise HTTPException(status_code=400, detail="시작일은 종료일보다 늦을 수 없습니다.")
    if (end_dt.date() - start_dt.date()).days > 90:
        raise HTTPException(status_code=400, detail="한 번에 수집할 수 있는 기간은 최대 90일입니다.")

    with db() as conn:
        market_row = require_owned_market(conn, u, body.market_id)
        source_market = str(market_row["market_type"]).strip().lower()
        market_name = str(market_row["alias"] or source_market)
        keys = get_market_key_payload(conn, u, body.market_id, source_market)

    if source_market == "cafe24":
        rows = await collect_cafe24_orders(keys, body.market_id, market_name, start_dt, end_dt, body.order_status)
    elif source_market == "coupang":
        rows = await collect_coupang_orders(keys, body.market_id, market_name, start_dt, end_dt, body.order_status)
    else:
        raise HTTPException(status_code=400, detail="CafeShipment 주문 수집은 Cafe24/쿠팡 마켓을 지원합니다.")
    with db() as conn:
        saved = upsert_cafeshipment_orders(conn, u["id"], rows)

    return {
        "ok": True,
        "marketId": body.market_id,
        "sourceMarket": source_market,
        "collected": len(rows),
        "saved": saved,
        "startDate": start_dt.strftime("%Y-%m-%d"),
        "endDate": end_dt.strftime("%Y-%m-%d"),
    }


@app.get("/api/cafeshipment/orders")
def cafeshipment_orders(request: Request, market_id: int | None = None, limit: int = 200, offset: int = 0):
    u = require_active_user(request)
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    q = """SELECT o.*, COALESCE(p.product_code, '') AS manual_product_code
           FROM cafeshipment_orders o
           LEFT JOIN cafeshipment_product_code_overrides p
             ON p.order_id=o.id AND p.user_id=?
           WHERE o.user_id=?"""
    count_q = "SELECT COUNT(*) c FROM cafeshipment_orders WHERE user_id=?"
    params: list[Any] = [u["id"], u["id"]]
    count_params: list[Any] = [u["id"]]
    if market_id is not None:
        q += " AND o.market_id=?"
        count_q += " AND market_id=?"
        params.append(market_id)
        count_params.append(market_id)
    q += " ORDER BY o.order_date DESC, o.id DESC LIMIT ? OFFSET ?"
    with db() as conn:
        total = conn.execute(count_q, count_params).fetchone()["c"]
        rows = conn.execute(q, [*params, limit, offset]).fetchall()
    return {
        "ok": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "orders": [cafeshipment_order_public(row) for row in rows],
    }


@app.post("/api/cafeshipment/orders/product-code")
def cafeshipment_save_product_code(body: CafeShipmentProductCodeBody, request: Request):
    u = require_active_user(request)
    order_id = int(body.order_id)
    if order_id <= 0:
        raise HTTPException(status_code=400, detail="주문을 선택하세요.")
    product_code = clean_manual_product_code(body.product_code)
    if not product_code:
        raise HTTPException(status_code=400, detail="저장할 상품코드를 입력하세요.")
    note = clean_override_note(body.note)
    ts = now()
    with db() as conn:
        order = conn.execute(
            "SELECT * FROM cafeshipment_orders WHERE id=? AND user_id=?",
            (order_id, u["id"]),
        ).fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")
        conn.execute(
            """INSERT INTO cafeshipment_product_code_overrides(
                   user_id, order_id, product_code, note, created_at, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(user_id, order_id) DO UPDATE SET
                   product_code=excluded.product_code,
                   note=excluded.note,
                   updated_at=excluded.updated_at""",
            (u["id"], order_id, product_code, note, ts, ts),
        )
        conn.commit()
    return {"ok": True, "orderId": order_id, "productCode": product_code}


@app.post("/api/cafeshipment/orders/export-clipboard")
def cafeshipment_orders_export_clipboard(body: CafeShipmentExportBody, request: Request):
    u = require_active_user(request)
    order_ids = [int(order_id) for order_id in body.order_ids if int(order_id) > 0]
    if not order_ids:
        raise HTTPException(status_code=400, detail="출고용으로 복사할 주문을 선택하세요.")
    placeholders = ",".join("?" for _ in order_ids)
    owner_clause = "" if u["is_admin"] else " AND o.user_id=?"
    params: list[Any] = [*order_ids]
    if not u["is_admin"]:
        params.append(u["id"])
    with db() as conn:
        rows = conn.execute(
            f"""SELECT o.*, COALESCE(p.product_code, '') AS manual_product_code
                FROM cafeshipment_orders o
                LEFT JOIN cafeshipment_product_code_overrides p
                  ON p.order_id=o.id AND p.user_id=o.user_id
                WHERE o.id IN ({placeholders}){owner_clause}""",
            params,
        ).fetchall()
    by_id = {row["id"]: row for row in rows}
    ordered_rows = [by_id[order_id] for order_id in order_ids if order_id in by_id]
    if not ordered_rows:
        raise HTTPException(status_code=404, detail="선택한 주문을 찾을 수 없습니다.")
    export_date = body.export_date or ""
    if not export_date:
        parsed_dates = [
            ShipmentRequestOrderExportFormatterEx_date
            for ShipmentRequestOrderExportFormatterEx_date in (
                parse_iso_date(row["order_date"][:10], datetime.now(timezone.utc)).strftime("%Y-%m-%d")
                for row in ordered_rows
                if row["order_date"]
            )
        ]
        export_date = max(parsed_dates) if parsed_dates else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    export_market_name = body.market_name.strip() or "홈런마켓"
    text, blank_count = build_shipment_clipboard(ordered_rows, export_market_name, export_date)
    preview_rows = build_shipment_export_preview(ordered_rows, export_market_name, export_date)
    return {
        "ok": True,
        "count": len(ordered_rows),
        "blankCount": blank_count,
        "clipboardText": text,
        "marketName": export_market_name,
        "exportDate": export_date,
        "previewRows": [
            {
                "orderId": row["orderId"],
                "sourceMarket": row["sourceMarket"],
                "productName": row["productName"],
                "optionText": row["optionText"],
                "baseProductCode": row["baseProductCode"],
                "manualProductCode": row["manualProductCode"],
                "productCode": row["productCode"],
                "quantity": row["quantity"],
                "recipientName": row["recipientName"],
            }
            for row in preview_rows
        ],
        "columns": [
            "공급사 상품명(매입상품명)",
            "상품옵션",
            " ",
            "수량",
            "수령인",
            "수령인 휴대전화",
            "수령인 우편번호",
            "수령인 주소",
            "배송메시지",
            "수령인 상세 주소",
        ],
    }


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
