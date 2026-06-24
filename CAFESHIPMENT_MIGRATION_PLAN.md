# CafeShipment HTML 전환 1차 분석

작성일: 2026-06-24

## 목표

기존 `Cafe24ShipmentManager.exe`를 바로 HTML로 갈아엎는 것이 아니라, 먼저 핵심 업무 로직을 `2000` 서버 API로 분리하고 이후 웹 화면을 붙인다.

최종 방향:

- 브라우저/런처 로그인은 `http://141.164.40.19:2000` 포털 인증 사용
- Cafe24, Coupang, Google 관련 키/토큰은 서버 저장
- PC/브라우저는 키 원문을 받지 않음
- 주문 수집, 매칭, 송장 반영 상태는 서버 DB에 저장
- HTML 화면은 서버 API를 호출하는 작업 화면 역할

## 현재 실행 구조

바로가기:

```text
C:\Users\rkghr\Desktop\Cafe24 송장 관리자.lnk
```

실행 대상:

```text
C:\Users\rkghr\AppData\Local\Programs\Cafe24ShipmentManager\MainApp\Cafe24ShipmentManager.exe
```

소스 위치:

```text
C:\Users\rkghr\Cafe24ShipmentManager
```

기술 스택:

- C# WinForms
- .NET 7 Windows
- SQLite
- Dapper
- ClosedXML
- Google Sheets API
- Cafe24 API
- 일부 Coupang API 클라이언트 존재

## 현재 프로그램 주요 흐름

진입점: `Program.cs`

1. `appsettings.json` 로드
2. SQLite `app.db` 연결
3. 로컬 로그인 화면 실행
4. 사용자별 키/설정 확인
5. Google Sheets 설정 로드
6. Cafe24 마켓 클라이언트 생성
7. `MainForm` 실행

현재 로컬 로그인은 `app_users` 테이블을 사용한다. 앞으로는 2000 서버의 `users/sessions`로 대체한다.

## 현재 로컬 설정/키 위치

설정 파일:

```text
C:\Users\rkghr\Cafe24ShipmentManager\appsettings.json
```

주요 설정:

- Google credential path
- Google Spreadsheet ID
- Google 기본 시트명
- Cafe24 마켓별 token file path
- Cafe24 기본 택배사 코드
- 주문 조회 기간
- SQLite DB 경로
- 로그 경로

기본 키 폴더:

```text
%USERPROFILE%\Desktop\key
```

Cafe24 토큰 파일 패턴:

```text
cafe24_token*.json
```

Google credential 기본:

```text
credentials.json
```

현재 사용자별 키/설정은 Windows DPAPI로 보호되어 `app_user_settings`에 저장된다.

서버 전환 시 이 구조는 `2000` 서버의 `markets`, `market_keys`로 이동한다.

## 현재 SQLite 테이블

### 업무 테이블

`shipment_source_rows`

- Google Sheet 또는 엑셀에서 읽은 송장/출고 원본 행
- 발주사, 송장번호, 수령인 연락처, 상품코드, 주문일, 택배사, 원본 데이터 저장

`cafe24_orders_cache`

- Cafe24/Coupang 등 마켓에서 수집한 주문 캐시
- 주문번호, 주문 아이템 코드, 수령인, 전화번호, 상품명, 수량, 주문일, 배송 코드, 원본 JSON 저장

`match_results`

- 출고 원본 행과 마켓 주문의 매칭 결과
- 자동 확정, 후보, 미매칭, 사용자 확정 상태 저장

`push_log`

- 송장 반영 요청/응답 로그
- 요청 body, 응답 body, HTTP 상태, 성공/실패 저장

`stock_inventory_cache`

- 재고/매입 관련 시트 캐시
- 상품코드, 공급사, 매입가, 재고, 구매 링크 등 저장

`stock_order_headers`, `stock_order_lines`

- 재고 발주 기록
- 발주 묶음과 상품별 발주 수량/단가 저장

`discontinued_products`

- 단종 상품 코드 저장

### 로컬 인증/설정 테이블

`app_users`

- 로컬 앱 사용자
- 서버 전환 후 `users`로 대체

`app_login_settings`

- 로컬 자동 로그인/비밀번호 저장
- 서버 전환 후 런처 토큰 또는 브라우저 세션으로 대체

`app_user_settings`

- 사용자별 Google/Cafe24/Coupang 설정
- 서버 전환 후 `markets`, `market_keys`로 대체

## 현재 화면 기능

`MainForm.cs`

- 주문 조회
- 주문 데이터 미리보기
- Google Sheets 출고정보 로드
- 발주사 필터
- 주문/송장 매칭
- 매칭 결과 표시
- 확정 항목 송장 반영
- 실패/결과 표시
- 사용자 설정 열기
- 로그아웃

`MainForm.OrderExport.cs`

- 선택 주문 출고용 복사
- 선택 주문 출고용 엑셀 저장
- 출고용 작업 메뉴
- 주문 진행상태 표시

`MainForm.Enhanced.cs`

- 재고 시트 조회
- 공급사/상품 필터
- 단종 표시
- 상품코드 복사
- 재고 발주 기록 저장
- 간단 차트/통계 표시

## 현재 외부 연동

### Cafe24

파일:

```text
Services\Cafe24ApiClient.cs
```

기능:

- 날짜 범위 주문 조회
- 주문상태 필터 조회
- 주문 item 단위 파싱
- 송장번호 반영
- AccessToken 재로드
- RefreshToken 갱신
- OAuth 재인증
- Cafe24 공유 토큰 파일 저장

주요 API:

- `GET /api/v2/admin/orders`
- `POST /api/v2/admin/orders/{order_id}/shipments`
- `POST /api/v2/oauth/token`

### Google Sheets

파일:

```text
Services\GoogleSheetsReader.cs
```

기능:

- OAuth2 인증
- 시트 목록 조회
- 발주사 목록 조회
- 출고정보 시트 읽기
- 발주사/날짜 필터 적용
- 헤더 자동 탐지
- 전화번호/송장/택배사 컬럼 탐지

서버 전환 시 선택지:

- 1차: 기존 Google OAuth를 PC/브라우저에 남기지 않고 서버 서비스 계정 방식으로 전환 검토
- 임시: Google credential/token을 서버에 저장하고 서버가 Sheets API 호출

### Coupang

파일:

```text
Services\CoupangApiClient.cs
```

기능:

- 쿠팡 주문 조회
- 주문상태 `ACCEPT`, `INSTRUCT` 기준 조회
- 송장 반영
- 택배사 코드 매핑
- 쿠팡 HMAC 서명

현재 2000 서버에는 이미 쿠팡 저장키 기반 프록시가 들어갔다. 이 로직으로 흡수 가능하다.

## HTML 전환 판단

처음부터 전체 HTML 재작성은 위험하다.

이유:

- 기능이 주문 수집, 시트 읽기, 매칭, 송장 반영, 엑셀 저장, 재고 발주까지 넓음
- 로컬 DB와 로컬 토큰에 상태가 많이 쌓임
- 기존 업무 규칙이 코드 안에 있음
- 화면만 HTML화하면 키 보안/멀티 PC 동기화 문제가 해결되지 않음

따라서 순서는 다음이 맞다.

1. 서버 DB/API 생성
2. 기존 로직을 서버 함수로 이식
3. 최소 HTML 화면 생성
4. 기존 EXE는 런처 또는 임시 백업 도구로 축소

## 1차 서버 API 범위

1차 목표:

브라우저에서 로그인 후 CafeShipment 페이지에서 주문 수집과 목록 확인까지 가능하게 한다.

필요 API:

```text
GET  /api/cafeshipment/config
POST /api/cafeshipment/orders/collect
GET  /api/cafeshipment/orders
POST /api/cafeshipment/source/import-sheet
POST /api/cafeshipment/match
GET  /api/cafeshipment/matches
POST /api/cafeshipment/shipments/push
GET  /api/cafeshipment/push-log
```

1차에서 가장 먼저 만들 API:

```text
POST /api/cafeshipment/orders/collect
GET  /api/cafeshipment/orders
```

이 두 개가 되면 HTML 화면에서 “주문 수집 → 주문 목록 표시”가 가능하다.

## 서버 DB로 옮길 테이블

2000 서버 SQLite에 추가할 후보:

```text
cafeshipment_orders
cafeshipment_source_rows
cafeshipment_matches
cafeshipment_push_log
cafeshipment_stock_inventory
cafeshipment_stock_order_headers
cafeshipment_stock_order_lines
cafeshipment_discontinued_products
```

모든 테이블에는 최소 아래 컬럼이 필요하다.

```text
user_id
market_id
created_at
updated_at
```

이유:

- 사용자별 데이터 분리
- 마켓별 주문/송장 상태 분리
- 집/다른 PC에서 작업해도 상태가 엉키지 않게 하기 위함

## 기존 로컬 기능의 서버 이동 순서

### 1단계: 주문 수집

이식 대상:

- `Cafe24ApiClient.FetchRecentOrders`
- `CoupangApiClient.FetchRecentOrders`
- `DatabaseManager.InsertOrderCache`

서버 결과:

- 저장된 `market_keys`로 Cafe24/Coupang 주문 조회
- 결과를 서버 DB에 저장
- HTML에서 주문 목록 표시

### 2단계: 출고정보 원본 가져오기

이식 대상:

- `GoogleSheetsReader.GetSheetList`
- `GoogleSheetsReader.FetchVendorList`
- `GoogleSheetsReader.ReadSheetFiltered`
- `DatabaseManager.BulkInsertSourceRows`

서버 결과:

- 서버가 Google Sheets에서 출고정보 읽기
- `cafeshipment_source_rows` 저장

### 3단계: 매칭

이식 대상:

- `MatchingEngine.ExecuteMatching`
- `MatchingEngine.ExecuteReverseMatching`
- `DatabaseManager.InsertMatchResult`

서버 결과:

- 주문과 출고정보를 서버에서 매칭
- HTML에서 후보/확정/미매칭 확인

### 4단계: 송장 반영

이식 대상:

- `Cafe24ApiClient.PushTrackingNumber`
- `CoupangApiClient.PushTrackingNumber`
- `DatabaseManager.InsertPushLog`
- `DatabaseManager.UpdateMatchStatus`

서버 결과:

- 서버가 저장키로 Cafe24/Coupang API 호출
- 반영 로그 서버 저장

### 5단계: 출고용 복사/엑셀 저장

이식 대상:

- `MainForm.OrderExport.cs`
- `ShipmentRequestOrderExportFormatterEx`

서버/HTML 결과:

- 선택 주문을 출고용 형식으로 생성
- 브라우저에서 복사 또는 XLSX 다운로드

### 6단계: 재고/발주 기능

이식 대상:

- `MainForm.Enhanced.cs`
- `stock_inventory_cache`
- `stock_order_headers`
- `stock_order_lines`

서버 결과:

- 1차 이후 별도 모듈로 확장

## 1차 HTML 화면 구성

CafeShipment 페이지:

- 상단: 마켓 선택, 날짜 범위, 주문상태 필터
- 버튼: 주문 수집
- 주문 목록 테이블
- 주문 상태/수집 시간 표시
- 선택 주문 출고용 복사 버튼은 2차에서 붙임

아직 1차에서 제외:

- 매칭 화면
- 송장 반영
- 재고 발주
- Google Sheets OAuth 재인증 UI
- 엑셀 저장

## 주의할 점

- 기존 `app.db`를 바로 서버 DB로 덮어쓰기보다 필요한 데이터만 마이그레이션해야 한다.
- Cafe24 AccessToken/RefreshToken은 서버 저장으로 옮겨야 한다.
- Google credential/token은 별도 보안 검토가 필요하다.
- 주문/송장 반영은 중복 실행 방지가 필요하다.
- 송장 반영은 실데이터에 영향을 주므로 dry-run 모드가 먼저 필요하다.

## 다음 작업

바로 다음 작업은 `2000` 서버에 CafeShipment 1차 DB/API를 추가하는 것이다.

작업 순서:

1. `landing-page-2000/app.py`에 `cafeshipment_orders` 테이블 추가
2. Cafe24 주문 수집용 서버 함수 추가
3. `POST /api/cafeshipment/orders/collect` 추가
4. `GET /api/cafeshipment/orders` 추가
5. HTML에 CafeShipment 주문 수집 화면 추가
6. 실제 Cafe24 키가 저장된 마켓으로 수집 테스트
