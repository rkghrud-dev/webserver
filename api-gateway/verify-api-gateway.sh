#!/usr/bin/env bash
set -euo pipefail

cd /opt/api-gateway
set -a
. ./.env
set +a

echo "--- health ---"
curl -sS http://localhost/health
echo

echo "--- test authorized ---"
curl -sS -H "X-Client-Token: ${CLIENT_TOKEN}" http://localhost/test
echo

echo "--- api-key authorized shape ---"
curl -sS -H "X-Client-Token: ${CLIENT_TOKEN}" http://localhost/api-key \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); d["api_key"]="<hidden>"; print(d)'

echo "--- api-key unauthorized status ---"
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost/api-key
