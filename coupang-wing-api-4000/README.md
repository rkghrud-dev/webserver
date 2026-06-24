# WebOCR Key Manager

Runs on port `4000` and saves WebOCR-compatible key files on the server.

## Key storage logic

- The screen is based on `WEBOCRV2_LOCAL\키설정도구.html`.
- Users enter `이름` and `쇼핑몰 이름`.
- The server assigns shop IDs like `S001`, `S002`, etc. because shop names are often Korean.
- Files are saved under `/opt/coupang-wing-api/data/key-manager/{SHOP_ID}/`.
- The saved file names and contents match the existing WebOCR key generator output.
- `shop_registry.json` maps Korean shop names to their generated `S###` IDs.

## Endpoints

- `GET /health`
- `GET /keys/login`
- `GET /keys/manage?name=NAME&shop_name=SHOP`
- `POST /api/key-files`
- `GET /api/shops`

Example saved files:

- `/opt/coupang-wing-api/data/key-manager/S001/coupang_wing_api.txt`
- `/opt/coupang-wing-api/data/key-manager/S001/cafe24_token_rkghrud1.json`
- `/opt/coupang-wing-api/data/key-manager/S001/홈런/쿠팡/coupang_wing_api.txt`
