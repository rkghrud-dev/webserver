# RK Work Portal API Contracts

이 문서는 `2000` 단일 서버 앱의 공통 계약이다. Web 프런트, WebOCR EXE, CafeShipment, 관리자 화면은 이 계약을 기준으로 붙인다.

## 서버 기준

- Base URL: `http://141.164.40.19:2000`
- Local folder: `C:\Users\rkghr\Desktop\서버\landing-page-2000`
- Runtime: FastAPI + SQLite + Docker Compose
- Truth source: 인증, 승인, 키, 업로드 상태, 마켓 API 호출은 모두 `2000` 서버가 담당한다.
- `80` api-gateway는 건드리지 않는다.
- `4000`은 폐기 상태이며 새 기능 기준으로 사용하지 않는다.

## 인증 방식

웹 브라우저:

- `POST /api/login` 성공 시 `sid` HttpOnly cookie가 설정된다.
- 이후 웹 요청은 쿠키로 인증한다.

PC 클라이언트:

- `POST /api/login` 성공 응답의 `token`을 저장한다.
- 이후 요청은 `Authorization: Bearer <token>` 헤더를 보낸다.

세션 만료 정책:

- 현재 기본 세션은 7일 쿠키 기준이다.
- PC 토큰도 같은 `sessions` 테이블을 사용한다.
- 추후 `expires_at` 컬럼을 추가해 만료를 명시할 수 있다.

## 공통 에러 형식

FastAPI 기본 형식을 사용한다.

```json
{
  "detail": "오류 메시지"
}
```

권장 HTTP status:

- `400`: 요청값 오류
- `401`: 로그인 필요 또는 인증 실패
- `403`: 권한 없음, 승인 필요, 비활성 계정
- `404`: 리소스 없음
- `409`: 중복 또는 상태 충돌
- `500`: 서버 오류

## 사용자 상태

`users.status`

- `pending`: 승인 대기
- `active`: 승인됨
- `disabled`: 비활성

`users.is_admin`

- `0`: 일반 회원
- `1`: 관리자

관리자는 `status=active`로 취급한다.

## 현재 구현된 DB

### users

```text
id INTEGER PRIMARY KEY
email TEXT UNIQUE NOT NULL
pw_hash TEXT NOT NULL
name TEXT
org TEXT
status TEXT NOT NULL
is_admin INTEGER NOT NULL
created_at TEXT NOT NULL
```

### sessions

```text
token TEXT PRIMARY KEY
user_id INTEGER NOT NULL
created_at TEXT NOT NULL
event_id INTEGER
```

### login_events

```text
id INTEGER PRIMARY KEY
user_id INTEGER NOT NULL
email TEXT
login_at TEXT NOT NULL
logout_at TEXT
```

### markets

현재 `shops` 역할의 1차 테이블이다. 추후 이름을 `shops`로 바꾸거나 alias를 유지한다.

```text
id INTEGER PRIMARY KEY
user_id INTEGER NOT NULL
alias TEXT NOT NULL
market_type TEXT NOT NULL
created_at TEXT NOT NULL
```

### market_keys

사용자/마켓별 키 저장. 평문 반환 금지.

```text
id INTEGER PRIMARY KEY
user_id INTEGER NOT NULL
market_id INTEGER
market TEXT NOT NULL
key_payload_encrypted TEXT NOT NULL
status TEXT NOT NULL
updated_at TEXT NOT NULL
```

현재 구현 완료.

### products

WebOCR이 만든 상품 기본 정보.

```text
id INTEGER PRIMARY KEY
user_id INTEGER NOT NULL
market_id INTEGER
product_code TEXT NOT NULL
product_name TEXT
option_name TEXT
source_file TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

### upload_batches

업로드 작업 묶음.

```text
id INTEGER PRIMARY KEY
user_id INTEGER NOT NULL
market_id INTEGER
batch_name TEXT
source_pc_name TEXT
status TEXT NOT NULL
created_at TEXT NOT NULL
completed_at TEXT
```

### market_uploads

상품별/마켓별 업로드 상태.

```text
id INTEGER PRIMARY KEY
batch_id INTEGER
product_id INTEGER
market TEXT NOT NULL
status TEXT NOT NULL
external_product_id TEXT
external_item_id TEXT
request_payload TEXT
response_payload TEXT
error_message TEXT
uploaded_at TEXT
updated_at TEXT NOT NULL
```

### cafeshipment_orders

CafeShipment 주문 수집 캐시. 사용자/마켓별로 분리 저장한다.

```text
id INTEGER PRIMARY KEY
user_id INTEGER NOT NULL
market_id INTEGER NOT NULL
source_market TEXT NOT NULL
mall_id TEXT
market_name TEXT
order_id TEXT NOT NULL
order_item_code TEXT
recipient_phone TEXT
recipient_name TEXT
recipient_cell_phone TEXT
order_status TEXT
product_name TEXT
order_amount REAL
quantity INTEGER
order_date TEXT
shipping_code TEXT
raw_json TEXT
collected_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

현재 구현 완료.

## 현재 구현 API

### GET /health

헬스체크.

Response:

```json
{ "ok": true }
```

### GET /

`index.html` 포털 화면 반환.

### POST /api/register

회원가입 요청. 가입 직후 상태는 `pending`.

Request:

```json
{
  "email": "user@example.com",
  "password": "password",
  "name": "홍길동",
  "org": "쇼핑몰명"
}
```

Response:

```json
{
  "ok": true,
  "status": "pending"
}
```

### POST /api/login

로그인. 웹 쿠키와 PC Bearer 토큰을 동시에 발급한다.

Request:

```json
{
  "email": "user@example.com",
  "password": "password"
}
```

Response:

```json
{
  "ok": true,
  "token": "session-token",
  "user": {
    "id": 1,
    "email": "user@example.com",
    "name": "홍길동",
    "org": "쇼핑몰명",
    "status": "active",
    "isAdmin": false,
    "active": true
  }
}
```

### POST /api/logout

현재 세션 종료.

웹은 쿠키의 `sid`, PC는 `Authorization: Bearer <token>` 기준으로 종료한다.

Response:

```json
{ "ok": true }
```

### GET /api/me

현재 로그인 사용자 조회.

Response:

```json
{
  "ok": true,
  "user": null
}
```

또는:

```json
{
  "ok": true,
  "user": {
    "id": 1,
    "email": "user@example.com",
    "name": "홍길동",
    "org": "쇼핑몰명",
    "status": "active",
    "isAdmin": false,
    "active": true
  }
}
```

### POST /api/profile

내 정보 변경. 로그인 필요.

Request:

```json
{
  "name": "홍길동",
  "org": "쇼핑몰명"
}
```

### POST /api/change-password

비밀번호 변경. 로그인 필요.

Request:

```json
{
  "currentPassword": "old",
  "newPassword": "new"
}
```

### GET /api/markets

내 마켓 목록. 로그인 필요.

### POST /api/markets

마켓 추가. 승인된 회원 또는 관리자만 가능.

Request:

```json
{
  "alias": "홈런몰",
  "market_type": "cafe24"
}
```

### DELETE /api/markets/{market_id}

내 마켓 삭제.

### GET /api/admin/members

관리자 전용 회원 목록.

Query:

- `status=pending|active|disabled`

### POST /api/admin/members/{user_id}/approve

회원 승인.

### POST /api/admin/members/{user_id}/reject

회원 거절. 현재는 `disabled`로 변경한다.

### POST /api/admin/members/{user_id}/disable

회원 비활성화.

### POST /api/admin/members/{user_id}/restore

회원 복구. 현재는 `active`로 변경한다.

### GET /api/admin/access-log

관리자 전용 접속 기록.

### GET /api/keys

마켓별 키 상태 조회. 평문 반환 금지.

Query:

- `market_id`: 선택. 특정 마켓의 키 상태만 조회.

Response:

```json
{
  "ok": true,
  "keys": [
    {
      "id": 1,
      "marketId": 1,
      "market": "coupang",
      "status": "saved",
      "saved": true,
      "fields": ["access_key", "secret_key", "vendor_id"],
      "updatedAt": "2026-06-24 13:00:00"
    }
  ]
}
```

### POST /api/keys

마켓별 키 저장. 서버에서 암호화 저장.

승인된 회원 또는 관리자만 가능하다.

Request:

```json
{
  "market_id": 1,
  "market": "coupang",
  "payload": {
    "access_key": "...",
    "secret_key": "...",
    "vendor_id": "..."
  }
}
```

Response:

```json
{
  "ok": true,
  "key": {
    "id": 1,
    "marketId": 1,
    "market": "coupang",
    "status": "saved",
    "saved": true,
    "fields": ["access_key", "secret_key", "vendor_id"],
    "updatedAt": "2026-06-24 13:00:00"
  }
}
```

응답에 키 원문은 절대 포함하지 않는다.

## 추가 예정 API

### POST /api/uploads

WebOCR PC 클라이언트가 상품 업로드 작업을 서버에 접수한다.

### GET /api/uploads

업로드 상태 조회.

### POST /api/market/{market}/proxy

서버가 저장된 키로 마켓 API를 호출한다. PC는 키를 받지 않는다.

현재 구현:

- `coupang`: 쿠팡 HMAC 서명을 서버에서 생성 후 호출
- `naver`: 네이버 커머스 액세스 토큰을 서버에서 발급 후 호출
- `cafe24`: 저장된 Cafe24 AccessToken으로 호출
- `lotteon`, `11st`, `esm`, `other`: 키 저장은 가능하지만 API 호출 어댑터는 추후 추가

Request:

```json
{
  "market_id": 1,
  "method": "GET",
  "path": "/v2/providers/openapi/apis/api/v4/vendors/A00000000/returnShippingCenters",
  "query": {},
  "body": null
}
```

Response:

```json
{
  "ok": true,
  "market": "coupang",
  "marketId": 1,
  "statusCode": 200,
  "data": {}
}
```

보안 제한:

- `path`는 전체 URL이 아니라 `/`로 시작하는 상대 경로만 허용한다.
- `://`, `..`, `\`가 포함된 경로는 거부한다.
- 마켓별 허용 prefix 밖의 경로는 거부한다.
- 키 원문은 응답에 포함하지 않는다.

마켓별 허용 path:

- 쿠팡: `/v2/providers/openapi/apis/api/`, `/v2/providers/seller_api/apis/api/`, `/v2/providers/marketplace_openapi/apis/api/`
- 네이버: `/v1/`
- Cafe24: `/api/v2/admin/`

### POST /api/cafeshipment/orders/collect

CafeShipment 주문 수집. 저장된 마켓 키를 서버에서 사용한다.

현재 구현은 `cafe24`, `coupang` 마켓 주문 수집을 지원한다.

Request:

```json
{
  "market_id": 1,
  "start_date": "2026-06-10",
  "end_date": "2026-06-24",
  "order_status": "N20,N21"
}
```

필드:

- `market_id`: 필수. 내 마켓 ID
- `start_date`: 선택. 없으면 최근 14일
- `end_date`: 선택. 없으면 오늘
- `order_status`: 선택. Cafe24 주문상태 코드 CSV 또는 쿠팡 주문상태 CSV
  - Cafe24 예: `N20,N21`
  - 쿠팡 예: `ACCEPT,INSTRUCT`
  - 쿠팡에서 비워두면 기본값은 `ACCEPT,INSTRUCT`

Response:

```json
{
  "ok": true,
  "marketId": 1,
  "sourceMarket": "cafe24",
  "collected": 12,
  "saved": 12,
  "startDate": "2026-06-10",
  "endDate": "2026-06-24"
}
```

제한:

- 승인된 회원 또는 관리자만 가능
- 저장된 Cafe24 또는 쿠팡 키가 있어야 함
- 한 번에 최대 90일 범위
- 주문은 `user_id + market_id + order_id + order_item_code` 기준으로 upsert

### GET /api/cafeshipment/orders

CafeShipment 주문 수집 결과 조회.

Query:

- `market_id`: 선택
- `limit`: 선택. 기본 200, 최대 1000
- `offset`: 선택

Response:

```json
{
  "ok": true,
  "total": 1,
  "limit": 200,
  "offset": 0,
  "orders": [
    {
      "id": 1,
      "marketId": 1,
      "sourceMarket": "cafe24",
      "mallId": "myshop",
      "marketName": "홈런마켓",
      "orderId": "20260624-000001",
      "orderItemCode": "20260624-000001-01",
      "recipientPhone": "0212345678",
      "recipientName": "홍길동",
      "recipientCellPhone": "01012345678",
      "orderStatus": "N20",
      "productName": "상품명",
      "orderAmount": 10000,
      "quantity": 1,
      "orderDate": "2026-06-24 10:00:00",
      "shippingCode": "",
      "collectedAt": "2026-06-24 13:00:00",
      "updatedAt": "2026-06-24 13:00:00"
    }
  ]
}
```

### GET /api/app/version

런처/EXE 업데이트 버전 확인.

Query:

- `app=launcher|webocr|cafeshipment`

### GET /downloads/{app}/{filename}

승인된 사용자만 다운로드 가능.

## 보안 원칙

- API 키 원문은 PC로 내려보내지 않는다.
- 키 입력 화면에서도 저장 후에는 마스킹 상태만 보여준다.
- 로그에 비밀번호, 토큰, API 키를 남기지 않는다.
- `.env`, DB, 키 파일은 git에 포함하지 않는다.
- 마켓 API 호출은 최종적으로 서버에서만 수행한다.
