# WebOCR 서버/PC 분리 구조 정리

## 핵심 방향

`WEBOCRV2_LOCAL` 전체를 서버에 올리는 것이 아니다.

WebOCR은 무거운 로컬 프로그램이므로 사용자의 PC에 EXE로 설치한다. 서버는 회원 인증, 권한, 키 보관, 업로드 상태, 마켓 API 호출을 중앙에서 담당한다.

최종 목표는 집 PC, 다른 PC, 노트북에서 작업해도 업로드 상태와 키가 꼬이지 않게 만드는 것이다.

## 최종 구조

```text
PC WebOCR EXE
  OCR / 이미지 / 엑셀 / 로컬 파일 처리
  상품 데이터 생성
  서버에 작업 요청

서버
  로그인 / 회원 승인 / 권한 확인
  유저별 마켓 키 보관
  네이버 / 쿠팡 / Cafe24 / 롯데ON API 호출
  업로드 성공/실패 상태 저장
  앱 버전 / 다운로드 파일 관리
```

## 왜 서버가 마켓 API 호출까지 해야 하는가

서버가 마켓 API 호출까지 담당하는 방식으로 간다.

이유:

- 키가 PC로 내려가지 않는다.
- 키 유출 위험이 줄어든다.
- 어떤 상품을 어느 마켓에 올렸는지 중앙에서 관리할 수 있다.
- 집 PC와 다른 PC에서 작업해도 업로드 상태가 하나로 통일된다.
- 중복 업로드를 막기 쉽다.
- 실패 상품만 다시 처리할 수 있다.
- 관리자 페이지에서 전체 작업 현황을 볼 수 있다.
- CafeShipment와 WebOCR이 같은 업로드 상태를 공유할 수 있다.

## 하면 안 되는 구조

```text
PC마다 key 폴더를 따로 둔다
PC마다 upload_history.json을 따로 쓴다
각 PC가 직접 쿠팡/네이버/Cafe24 API를 호출한다
서버는 단순 다운로드 페이지만 제공한다
```

이 구조는 PC가 여러 대가 되는 순간 꼬인다.

예:

- 집 PC에서는 이미 올렸다고 나오는데 다른 PC에서는 모름
- 같은 상품을 다른 PC에서 다시 올릴 수 있음
- 키가 여러 PC에 퍼짐
- 업로드 실패/성공 이력이 흩어짐
- 어떤 마켓 상품번호가 최종값인지 알기 어려움

## 맞는 구조

```text
PC WebOCR
  상품 데이터 생성
  이미지/엑셀/OCR 처리
  서버에 업로드 요청

서버
  로그인 확인
  회원 승인 상태 확인
  마켓 권한 확인
  서버에 저장된 키로 마켓 API 호출
  결과를 DB에 저장
  PC에 결과만 반환
```

예:

```text
상품 GS001 업로드 요청
  ↓
서버가 권한 확인
  ↓
서버가 네이버 API 호출
  ↓
서버가 쿠팡 API 호출
  ↓
서버 DB에 결과 저장
  ↓
PC 화면에 성공/실패 표시
```

## PC에 설치될 것

PC 설치용 EXE 패키지에는 아래가 들어간다.

- RKLauncher.exe
- WebOCR.exe
- OCR 처리 코드
- 이미지 처리 코드
- 엑셀 읽기/쓰기 코드
- 로컬 파일 선택/저장 기능
- WebView2 또는 HTML UI
- 자동 업데이트 기능

PC가 담당하는 일:

- 로컬 이미지 파일 접근
- 로컬 엑셀 파일 접근
- OCR 처리
- 상품 후보 데이터 생성
- 사용자가 보는 작업 화면 표시
- 서버에 작업 요청 전송

## 서버에 올라갈 것

서버에는 아래가 올라간다.

- 회원가입/로그인/승인 웹페이지
- EXE 다운로드 페이지
- 앱 버전 확인 API
- 업데이트 파일
- 사용자/마켓/권한 DB
- 마켓별 키 저장 DB
- 업로드 상태 DB
- 네이버/쿠팡/Cafe24/롯데ON API 호출 로직
- 관리자 페이지

서버가 담당하는 일:

- 회원 가입
- 관리자 승인
- 로그인 토큰 발급
- 사용자별 권한 확인
- 마켓별 API 키 암호화 저장
- 상품 업로드 요청 접수
- 마켓 API 호출
- 업로드 결과 저장
- 중복 업로드 방지
- 실패 이력 관리
- EXE 최신 버전 제공

## 서버에 저장할 핵심 데이터

### users

회원 정보.

```text
id
login_id
password_hash
name
role
status
created_at
last_login_at
```

상태:

- pending: 승인 대기
- active: 승인됨
- disabled: 비활성

역할:

- user
- admin

### shops

사용자별 쇼핑몰/마켓 묶음.

```text
id
user_id
shop_name
status
created_at
updated_at
```

예:

- 홈런마켓
- 준비몰
- 기타 사용자 쇼핑몰

### market_keys

마켓별 키 저장.

```text
id
user_id
shop_id
market
key_payload_encrypted
status
updated_at
```

market 예:

- naver
- coupang
- cafe24
- lotteon
- elevenst
- esm

키는 원문 저장이 아니라 암호화 저장을 기준으로 한다.

### products

WebOCR이 만든 상품 기본 정보.

```text
id
user_id
shop_id
product_code
product_name
option_name
source_file
created_at
updated_at
```

### upload_batches

한 번의 업로드 작업 묶음.

```text
id
user_id
shop_id
batch_name
source_pc_name
status
created_at
completed_at
```

### market_uploads

상품별/마켓별 업로드 상태.

```text
id
batch_id
product_id
market
status
external_product_id
external_item_id
request_payload
response_payload
error_message
uploaded_at
updated_at
```

status 예:

- pending
- uploading
- success
- failed
- skipped
- duplicate

예:

```text
GS001
  네이버: success / originProductNo=123456
  쿠팡: failed / category error
  Cafe24: success / product_no=789
  롯데ON: pending
```

## 로그인/실행 흐름

```text
사용자 브라우저 접속
  ↓
회원가입
  ↓
관리자 승인
  ↓
EXE 다운로드 가능
  ↓
RKLauncher.exe 실행
  ↓
로그인
  ↓
서버가 회원/권한 확인
  ↓
버전 확인
  ↓
업데이트 필요 시 다운로드/교체
  ↓
WebOCR 실행
```

## WebOCR 작업 흐름

```text
WebOCR EXE 실행
  ↓
서버 로그인 토큰 확인
  ↓
사용자/마켓 선택
  ↓
로컬 파일 선택
  ↓
OCR / 엑셀 / 이미지 처리
  ↓
상품 데이터 생성
  ↓
서버에 업로드 요청
  ↓
서버가 마켓 API 호출
  ↓
서버가 업로드 결과 저장
  ↓
WebOCR 화면에 결과 표시
```

## 키 처리 원칙

키는 서버에 저장한다.

가능하면 PC로 키 원문을 내려보내지 않는다.

```text
좋은 방식:
PC -> 서버에 작업 요청
서버 -> 저장된 키로 마켓 API 호출
서버 -> 결과만 PC에 반환
```

```text
피해야 할 방식:
서버 -> PC에 API 키 원문 전달
PC -> 직접 마켓 API 호출
PC -> 업로드 결과를 따로 서버에 보고
```

초기에는 기존 WebOCR 구조 때문에 일부 키를 PC에서 쓰는 과도기가 있을 수 있다. 하지만 최종 목표는 마켓 API 호출을 서버로 이전하는 것이다.

## 앱 업데이트 구조

서버는 최신 버전 정보를 제공한다.

```text
GET /api/app/version?app=webocr
```

응답 예:

```json
{
  "app": "webocr",
  "latest_version": "0.1.0",
  "download_url": "/downloads/webocr/WebOCR_0.1.0.zip",
  "sha256": "파일검증값",
  "required": true
}
```

런처는 실행 시:

1. 현재 버전을 서버에 보낸다.
2. 최신 버전을 확인한다.
3. 업데이트가 있으면 다운로드한다.
4. 파일 해시를 검증한다.
5. 기존 파일을 교체한다.
6. 최신 WebOCR을 실행한다.

## 4000 포트 역할

앞으로 `4000` 포트는 새 메인 서버 앱으로 사용한다.

역할:

- 회원가입
- 로그인
- 승인 상태 확인
- EXE 다운로드
- 앱 버전 확인
- 사용자별 키 설정
- 업로드 상태 조회
- WebOCR/CafeShipment 서버 API
- 관리자 페이지

## 2000 포트 역할

`2000` 포트는 임시 랜딩/테스트용으로 둔다.

나중에 정리하면:

- `4000`: 실제 앱
- `2000`: 임시/개발/구버전
- `80`: 도메인 연결 후 리버스 프록시 또는 기본 진입점

## 단계별 구현 순서

### 1단계: 서버 기본 뼈대

- 4000 포트 새 앱 생성
- 회원가입/로그인
- 관리자 승인
- 승인된 회원만 EXE 다운로드

### 2단계: 런처 연결

- RKLauncher.exe 로그인 API 연결
- 회원 승인 상태 확인
- 버전 확인 API 연결
- 업데이트 다운로드 구조 추가

### 3단계: 키 저장

- 사용자/마켓별 키 입력 페이지
- 키 암호화 저장
- 키 상태 표시
- 필수/선택값 검증

### 4단계: 업로드 상태 중앙화

- WebOCR이 만든 상품 데이터를 서버로 전송
- upload_batches / products / market_uploads 저장
- PC가 여러 대여도 같은 상태를 보게 만들기

### 5단계: 마켓 API 서버 호출

- 네이버 API 서버 호출
- 쿠팡 API 서버 호출
- Cafe24 API 서버 호출
- 롯데ON API 서버 호출
- 실패/성공 결과 DB 저장

### 6단계: CafeShipment 연동

- CafeShipment도 같은 회원/마켓/업로드 상태 DB 사용
- 주문 수집 결과와 WebOCR 업로드 상품 매칭
- 누락/실패/재처리 큐 관리

## 결론

WebOCR은 PC에 설치되는 무거운 실행 프로그램으로 유지한다.

서버는 단순 다운로드 페이지가 아니라 중앙 본부 역할을 한다.

가장 중요한 기준:

```text
PC는 작업을 만든다.
서버는 권한, 키, 업로드 실행, 상태를 관리한다.
```

이 구조로 가야 여러 PC에서 작업해도 데이터가 엉키지 않는다.
