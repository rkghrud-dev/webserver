import os
import time
import hmac
import hashlib
from typing import Any
from urllib.parse import urlencode

from dotenv import load_dotenv
import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

app = FastAPI()

CLIENT_TOKEN = os.getenv("CLIENT_TOKEN", "")
API_BASE_URL = os.getenv("API_BASE_URL", "")
API_KEY = os.getenv("API_KEY", "")
API_AUTH_HEADER = os.getenv("API_AUTH_HEADER", "Authorization")
API_AUTH_PREFIX = os.getenv("API_AUTH_PREFIX", "")
COUPANG_BASE_URL = os.getenv("COUPANG_BASE_URL", "https://api-gateway.coupang.com")
COUPANG_ACCESS_KEY = os.getenv("COUPANG_ACCESS_KEY", "")
COUPANG_SECRET_KEY = os.getenv("COUPANG_SECRET_KEY", "")
COUPANG_VENDOR_ID = os.getenv("COUPANG_VENDOR_ID", "")
COUPANG_MARKET = os.getenv("COUPANG_MARKET", "KR")

COUPANG_ALLOWED_PATH_PREFIXES = (
    "/v2/providers/openapi/apis/api/",
)


class CoupangProxyRequest(BaseModel):
    method: str = Field("GET", description="GET, POST, PUT, PATCH, or DELETE")
    path: str = Field(..., description="Coupang API path only, not a full URL")
    query: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] | list[Any] | None = None


def require_client_token(x_client_token: str | None) -> None:
    if not CLIENT_TOKEN or x_client_token != CLIENT_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_coupang_config() -> None:
    missing = [
        name
        for name, value in {
            "COUPANG_ACCESS_KEY": COUPANG_ACCESS_KEY,
            "COUPANG_SECRET_KEY": COUPANG_SECRET_KEY,
            "COUPANG_VENDOR_ID": COUPANG_VENDOR_ID,
        }.items()
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail={"message": "Coupang API is not configured", "missing": missing},
        )


def build_coupang_authorization(method: str, path: str, query: dict[str, Any]) -> str:
    signed_date = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    query_string = urlencode(query, doseq=True)
    message = f"{signed_date}{method.upper()}{path}{query_string}"
    signature = hmac.new(
        COUPANG_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        "CEA algorithm=HmacSHA256, "
        f"access-key={COUPANG_ACCESS_KEY}, "
        f"signed-date={signed_date}, "
        f"signature={signature}"
    )


def validate_coupang_path(path: str) -> None:
    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail="Coupang path must start with /")
    if "://" in path or ".." in path:
        raise HTTPException(status_code=400, detail="Invalid Coupang path")
    if not any(path.startswith(prefix) for prefix in COUPANG_ALLOWED_PATH_PREFIXES):
        raise HTTPException(status_code=400, detail="Coupang path is not allowed")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/test")
def test(x_client_token: str | None = Header(None)):
    require_client_token(x_client_token)
    return {"ok": True, "message": "api gateway is working"}


@app.get("/api-key")
def get_api_key(x_client_token: str | None = Header(None)):
    require_client_token(x_client_token)

    if not API_KEY:
        raise HTTPException(status_code=404, detail="API key is not configured")

    return {
        "ok": True,
        "base_url": API_BASE_URL,
        "auth_header": API_AUTH_HEADER,
        "auth_prefix": API_AUTH_PREFIX,
        "api_key": API_KEY,
    }


@app.get("/coupang/status")
def coupang_status(x_client_token: str | None = Header(None)):
    require_client_token(x_client_token)
    return {
        "ok": True,
        "base_url": COUPANG_BASE_URL,
        "market": COUPANG_MARKET,
        "configured": {
            "access_key": bool(COUPANG_ACCESS_KEY),
            "secret_key": bool(COUPANG_SECRET_KEY),
            "vendor_id": bool(COUPANG_VENDOR_ID),
        },
    }


@app.post("/coupang/proxy")
async def coupang_proxy(
    request: CoupangProxyRequest,
    x_client_token: str | None = Header(None),
):
    require_client_token(x_client_token)
    require_coupang_config()

    method = request.method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise HTTPException(status_code=400, detail="Unsupported method")
    validate_coupang_path(request.path)

    authorization = build_coupang_authorization(method, request.path, request.query)
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json;charset=UTF-8",
        "X-Requested-By": COUPANG_VENDOR_ID,
        "X-MARKET": COUPANG_MARKET,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method=method,
            url=f"{COUPANG_BASE_URL}{request.path}",
            params=request.query,
            json=request.body if method != "GET" else None,
            headers=headers,
        )

    try:
        data = response.json()
    except ValueError:
        data = response.text

    return {
        "ok": response.is_success,
        "status_code": response.status_code,
        "data": data,
    }
