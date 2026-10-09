#!/usr/bin/env bash
# Deploy nhanh invoice-billing lên VPS (1 server, 1 domain, Docker Compose).
# Chạy 1 lần trên VPS mới sau khi clone+checkout exp1:
#   chmod +x scripts/deploy.sh && sudo ./scripts/deploy.sh
#
# Yêu cầu trước: đã có domain trỏ IP VPS, firewall đang mở 80/443/443 outbound.
set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "Usage: sudo $0 yourdomain.com"
  exit 1
fi

# 1. .env production (xóa secrets mẫu)
cp -n .env.example .env
SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
python3 - << PY
import re
p = ".env"
s = open(p).read()
s = s.replace("# JWT_SECRET=dev-secret-change-me-please-32bytes-min",
              "JWT_SECRET=$SECRET")
s = s.replace("APP_URL=http://localhost:8000", "APP_URL=https://$DOMAIN")
s = s.replace("CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173",
              "CORS_ORIGINS=https://$DOMAIN")
s = s.replace("# SITE_ADDRESS=", "SITE_ADDRESS=")
import re
# thay toàn bộ block VAPID comment thành real key
s = re.sub(r"^# VAPID_PUBLIC_KEY=.+\n# VAPID_PRIVATE_KEY=.+\n# VAPID_SUBJECT=.+",
           "VAPID_PUBLIC_KEY=" + __import__("subprocess").check_output(
               ["python3","scripts/gen_vapid_keys.py"]).decode().split("VAPID_PUBLIC_KEY=")[1].splitlines()[0].strip(),
           s, flags=re.M)
open(p,"w").write(s)
print("JWT_SECRET + APP_URL + CORS_ORIGINS + VAPID đã set trong .env")
PY

# 2. Build + chạy (Caddy tự lấy cert HTTPS)
JWT_SECRET=$(grep "^JWT_SECRET=" .env | cut -d= -f2-)
SITE_ADDRESS=$DOMAIN docker compose up -d --build

sleep 8
echo "=== Kiểm tra ==="
curl -sS "https://$DOMAIN/health" && echo " ✅ HTTPS app đã chạy"
echo "Mở $DOMAIN trên trình duyệt, đăng ký → bật Thông báo → Gửi thử."
