# RK 시스템 병렬 개발 로드맵 (Codex 작업 분배용) — 2000 단일서버 버전

> 변경: **4000 폐기.** 모든 서버 기능을 **2000(`landing-page-2000`) 하나**가 담당한다.
> (기존 `coupang-wing-api-4000` 키매니저 기능은 2000으로 흡수)
> 핵심 원칙: **한 창 = 정해진 파일/폴더만 수정.** 경계가 안 겹치면 병렬 충돌이 없다.

---

## 0. 역할 지도 (위치 / 폴더 / 담당 창)

| 위치 | 폴더 | 역할 | 담당 |
|---|---|---|---|
| 80 | `api-gateway` | 기존 게이트웨이 | ❌ 건드리지 않음 |
| **2000** | `landing-page-2000` | **단일 메인 서버**: 웹 포털 UI + 인증·승인·키·업로드상태·마켓API프록시·버전/다운로드·관리자 | 창 #1(백엔드) · 창 #2(프런트) |
| PC | `WEBOCRV2_LOCAL/webocrcludev2` | **WebOCR 클라이언트**(정리→EXE). 2000 호출 | 창 #3 |
| PC | `Cafe24ShipmentManager` | **CafeShipment**(C#). 2000 호출 | 창 #4 |
| PC | `RKLauncher` (신규, 후순위) | 런처(로그인·버전·업데이트) | 후순위 |

> 진실의 원천(인증·키·업로드 상태) = **오직 2000**. PC 클라이언트는 2000을 호출만 한다.
> 2000 안에서 **백엔드(`app.py` 계열)** 와 **프런트(`index.html`)** 를 두 창이 파일로 나눠 병렬 작업한다.

---

## STEP 0 — 공유 계약 먼저 (제일 먼저, 한 창에서 짧게)

`서버/CONTRACTS.md` 작성. 이게 있어야 #1~#4가 병렬로 같은 인터페이스를 본다.
- **인증**: `POST /api/login` → 세션쿠키(웹) + `token`(PC 클라용 Bearer). 상태 `pending|active|disabled`, 역할 `user|admin`.
- **DB 스키마**(2000 소유): `users / shops / market_keys / products / upload_batches / market_uploads` (필드는 `WEBOCR_SERVER_ARCHITECTURE.md` 그대로). ※ `users/markets(=shops)/login_events`는 이미 존재 → 확장.
- **API 시그니처**: 창 #1 표의 엔드포인트 요청/응답 JSON을 여기 고정.
- **에러 형식**: `{ "detail": "..." }` + HTTP status.

---

## Codex 창 #1 — 2000 백엔드 (`landing-page-2000`, **`app.py` 계열만**)

> 담당 파일: `app.py`, (분리 시) `routes_*.py`/`services_*.py`/`db.py`, `requirements.txt`, `Dockerfile`, `docker-compose.yml`. **`index.html`은 건드리지 않는다.**

1. **모듈 분리** — 비대해질 `app.py`를 `db.py / auth / keys / uploads / markets(proxy) / version / admin` 모듈로 분리(유지보수+병렬 대비)
2. **인증/승인/회원/마켓/접속기록** — 이미 구현됨 → 정리·유지, PC용 Bearer 토큰 추가
3. **샵·키 저장** — `shops`(=현 markets 확장), `market_keys`(암호화) + `GET/POST /api/keys` (마켓별 키, 평문 미반환)
4. **업로드 상태 DB/API** — `products / upload_batches / market_uploads` + `POST /api/uploads`(접수), `GET /api/uploads`(현황), 중복 방지
5. **마켓 API 프록시** — 서버가 저장 키로 `naver/coupang/cafe24/lotteon` 호출 → 결과 `market_uploads` 저장
6. **버전/다운로드** — `GET /api/app/version?app=`, `/downloads/...`, 파일 해시
7. **관리자 API** — 전체 업로드 현황, 회원별 권한·다운로드 권한

---

## Codex 창 #2 — 2000 프런트 (`landing-page-2000`, **`index.html`만**)

> 담당 파일: `index.html`(및 분리 시 정적 자원). **`app.py`는 건드리지 않는다.** 백엔드가 늦으면 계약(CONTRACTS.md) 기준 목으로 선개발.

1. **로그인/홈/회원관리/마켓/접속기록/내정보** — 이미 구현됨 → 유지
2. **키 설정 페이지** — `/api/keys` 연결 (마켓별 키 입력/상태 배지)
3. **WebOCR 다운로드 페이지** — 버전 API 연결, 승인자만 EXE 버튼
4. **업로드 현황 대시보드** — `/api/uploads` 연결 (상품·마켓별 성공/실패/재처리)
5. **관리자 권한 설정 화면** — 회원별 권한·다운로드 권한

---

## Codex 창 #3 — WebOCR 클라이언트 (`WEBOCRV2_LOCAL/webocrcludev2`)

1. **찌꺼기 정리** — 안 쓰는 엔드포인트/죽은 코드/옛 실험/임시 데이터 제거
2. **로그인 게이트** — 2000 인증으로 통일 (로컬 중계 유지)
3. **키: 로컬 폴더 → 서버** — `key/` 평문 로딩 제거, 2000 `/api/keys` 사용(과도기 허용)
4. **업로드: 서버 위임** — PC 직접 마켓호출 X → 2000 `/api/uploads`로 요청, 결과만 표시
5. **EXE 패키징 + 자동업데이트** — 런처 연동(버전 API)

---

## Codex 창 #4 — CafeShipment (`Cafe24ShipmentManager`, C#)

1. **키: 로컬 파일 → 서버** — `바탕화면\key\*` 로딩 제거, 2000 `/api/keys` 사용
2. **업로드/주문 상태 공유** — 같은 2000 업로드 상태 DB 사용
3. **WebOCR 업로드 상품과 매칭** — 누락/실패/재처리 큐
4. (후순위) 서버 구동형 분리 검토

---

## 의존 순서

```
STEP0 계약 ─▶ 창#1 (1 모듈분리 → 2 인증/토큰 → 3 키)
                 │
                 ├─▶ 창#2 (계약 기준 화면, 백엔드 붙으면 연결)
                 ├─▶ 창#3 WebOCR (폴더 분리 → 즉시 병렬)
                 └─▶ 창#4 CafeShipment (폴더 분리 → 즉시 병렬)
통합: 인증 → 키 → 업로드상태 → 마켓프록시 → CafeShipment 연동
```

- **먼저(직렬)**: STEP0 → 창#1의 1~3(모듈분리·토큰·키)
- **그 후 병렬**: 창#1(4~7) ∥ 창#2 ∥ 창#3 ∥ 창#4

---

## 충돌 방지 규칙 (병렬 필수)

- **창 #1 = `app.py`/백엔드 모듈/Docker·requirements 만.** `index.html` 금지.
- **창 #2 = `index.html`(정적 자원) 만.** `app.py` 금지.
- 창 #3 = `webocrcludev2` 폴더만 / 창 #4 = `Cafe24ShipmentManager` 폴더만.
- 같은 2000 폴더의 두 창은 **자기 파일만 `git add`** (또는 브랜치 `feat/portal-backend`·`feat/portal-frontend` 분리 후 병합 — 파일이 안 겹쳐 충돌 거의 없음).
- 공통 변경은 **`CONTRACTS.md`에만**, 바꾸기 전 합의.
- 비밀(`.env`)·DB·키파일은 커밋 금지(`.gitignore` 유지). **80 게이트웨이는 누구도 건드리지 않음.**
