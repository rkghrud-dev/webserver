#!/usr/bin/env bash
set -euo pipefail

cd /opt/coupang-wing-api

if [ ! -f .env ]; then
  if [ -f /opt/api-gateway/.env ]; then
    CLIENT_TOKEN="$(grep '^CLIENT_TOKEN=' /opt/api-gateway/.env | head -n1 | cut -d= -f2-)"
  else
    CLIENT_TOKEN=""
  fi
  {
    printf 'CLIENT_TOKEN=%s\n' "$CLIENT_TOKEN"
    printf 'ADMIN_PASSWORD=%s\n' "$CLIENT_TOKEN"
    printf 'SECRETS_PATH=/data/coupang-secrets.json\n'
    printf 'COUPANG_BASE_URL=https://api-gateway.coupang.com\n'
  } > .env
  chmod 600 .env
fi

read -r -p "Coupang access key: " COUPANG_ACCESS_KEY
read -r -s -p "Coupang secret key: " COUPANG_SECRET_KEY
printf '\n'
read -r -p "Coupang vendor id: " COUPANG_VENDOR_ID

python3 - <<'PY'
from pathlib import Path
import os

env_path = Path(".env")
values = {}
for line in env_path.read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        values[key] = value

values["COUPANG_BASE_URL"] = values.get("COUPANG_BASE_URL") or "https://api-gateway.coupang.com"

ordered = [
    "CLIENT_TOKEN",
    "ADMIN_PASSWORD",
    "SECRETS_PATH",
    "COUPANG_BASE_URL",
]
env_path.write_text("\n".join(f"{key}={values.get(key, '')}" for key in ordered) + "\n")

Path("data").mkdir(exist_ok=True)
Path("data/coupang-secrets.json").write_text(json.dumps({
    "base_url": values["COUPANG_BASE_URL"],
    "access_key": os.environ["COUPANG_ACCESS_KEY"],
    "secret_key": os.environ["COUPANG_SECRET_KEY"],
    "vendor_id": os.environ["COUPANG_VENDOR_ID"],
}, ensure_ascii=False, indent=2) + "\n")
PY

chmod 600 .env
chmod 600 data/coupang-secrets.json
docker compose up -d --build
echo "Saved Coupang secrets to /opt/coupang-wing-api/.env and restarted the service."
