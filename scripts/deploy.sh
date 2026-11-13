#!/usr/bin/env bash
# Deploy Quanh Ta lên VPS (1 server, 1 domain, Docker Compose + Caddy tự lấy cert).
# Chạy 1 lần trên VPS mới sau khi clone:
#   chmod +x scripts/deploy.sh && sudo ./scripts/deploy.sh yourdomain.com
#
# Yêu cầu trước: domain đã trỏ về IP VPS, firewall mở 80/443.
set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "Usage: sudo $0 yourdomain.com"
  exit 1
fi

# 1. .env production: sinh secret thật + trỏ domain (không ghi đè .env đã có)
cp -n .env.example .env
SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
DOMAIN="$DOMAIN" SECRET="$SECRET" python3 - <<'PY'
import os, re

domain, secret = os.environ["DOMAIN"], os.environ["SECRET"]
text = open(".env").read()

# Thay cả dòng (kể cả bản đang bị comment) để không còn secret mặc định.
text = re.sub(r"^#?\s*JWT_SECRET=.*$", f"JWT_SECRET={secret}", text, count=1, flags=re.M)
text = re.sub(r"^#?\s*APP_ENV=.*$", "APP_ENV=production", text, count=1, flags=re.M)
text = re.sub(r"^#?\s*APP_DEBUG=.*$", "APP_DEBUG=0", text, count=1, flags=re.M)
text = re.sub(r"^#?\s*APP_URL=.*$", f"APP_URL=https://{domain}", text, count=1, flags=re.M)
text = re.sub(r"^#?\s*CORS_ORIGINS=.*$", f"CORS_ORIGINS=https://{domain}", text, count=1, flags=re.M)
text = re.sub(r"^#?\s*SITE_ADDRESS=.*$", f"SITE_ADDRESS={domain}", text, count=1, flags=re.M)
open(".env", "w").write(text)
print("Đã set JWT_SECRET, APP_ENV=production, APP_URL, CORS_ORIGINS, SITE_ADDRESS")
PY

# 2. Build + chạy (Caddy tự xin cert HTTPS)
SITE_ADDRESS=$DOMAIN docker compose up -d --build

sleep 8
echo "=== Kiểm tra ==="
curl -sS "https://$DOMAIN/health" && echo " ✅ HTTPS app đã chạy"
echo "Mở https://$DOMAIN, đăng ký tài khoản → ghi khoản chi đầu tiên."
