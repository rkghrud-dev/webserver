# Server Context

This workspace is for managing the Vultr API gateway server.

## Server

- Provider: Vultr
- Region: Seoul, KR
- IP: 141.164.40.19
- OS: Ubuntu 24.04 LTS
- SSH user: root
- SSH command: `ssh -i ~/.ssh/vultr_api_gateway_deploy root@141.164.40.19`

## Current App

- Path on server: `/opt/api-gateway`
- Runtime: Docker Compose
- Public health URL: `http://141.164.40.19/health`
- Expected health response: `{"ok":true}`
- Container name: `api-gateway`
- API key endpoint: `GET http://141.164.40.19/api-key`
- API key endpoint auth: send `X-Client-Token` header with the server `CLIENT_TOKEN`

## WebOCR Key Manager / Coupang API App

- Path on server: `/opt/coupang-wing-api`
- Runtime: Docker Compose
- Public URL: `http://141.164.40.19:4000`
- Health URL: `http://141.164.40.19:4000/health`
- Key manager login: `http://141.164.40.19:4000/keys/login`
- Container name: `coupang-wing-api`

### WebOCR key storage

- The UI is based on `C:\Users\rkghr\Desktop\WEBOCRV2_LOCAL\키설정도구.html`.
- User enters `이름` + `쇼핑몰 이름`; the server assigns a shop ID like `S001`.
- Files are stored under `/opt/coupang-wing-api/data/key-manager/{SHOP_ID}/`.
- Example: `/opt/coupang-wing-api/data/key-manager/S001/coupang_wing_api.txt`
- The registry file `/opt/coupang-wing-api/data/key-manager/shop_registry.json` maps Korean shop names to `S###`.
- Saved key file names and contents match the existing WebOCR ZIP generator output.

## Useful Server Commands

```bash
ssh -i ~/.ssh/vultr_api_gateway_deploy root@141.164.40.19
cd /opt/api-gateway
docker ps
docker logs api-gateway --tail=80
docker compose up -d --build
docker compose down
curl http://localhost/health
curl -H "X-Client-Token: YOUR_CLIENT_TOKEN" http://141.164.40.19/api-key
cd /opt/coupang-wing-api
docker ps
docker logs coupang-wing-api --tail=80
curl http://localhost:4000/health
curl -H "X-Client-Token: YOUR_CLIENT_TOKEN" http://localhost:4000/status
```

## Goal

Use this server as a stable API/backend gateway. API keys should be stored on the server, not on local PCs.

When building new features such as a bulletin board, admin panel, API proxy, or automation tool:

- Keep secrets on the server.
- Use Docker Compose where practical.
- Confirm the service works from both localhost and the public IP.
- Avoid deleting existing server files unless explicitly requested.
