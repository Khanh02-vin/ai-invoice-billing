# Chạy thử Quanh Ta từ máy cá nhân (không cần mua VPS/domain)

Trạng thái hiện tại: **app đang public qua Cloudflare Quick Tunnel**, chạy trên chính máy này.
Người khác mở link là dùng được ngay. Không tốn tiền, không cần port forwarding, không cần mua domain.

## Link hiện tại

```
https://informational-pursue-posing-cas.trycloudflare.com
```

⚠️ **Link thay đổi mỗi lần khởi động tunnel.** Khi restart máy hoặc tunnel chết, xem link mới tại:
```bash
grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log | head -1
```

## Cấu trúc đang chạy

```
Trình duyệt người dùng
   │ HTTPS
   ▼
Cloudflare Tunnel  (trycloudflare.com)
   │ http://127.0.0.1:5175
   ▼
Vite dev server  (:5175)   ← serve giao diện + proxy /auth /spends /places /discover
   │ http://127.0.0.1:8005
   ▼
Backend Rust/Axum (:8005)  ──► data/spends.db
```

## Khởi động lại từ đầu (khi tunnel chết / restart máy)

Chạy lần lượt:

```bash
cd /home/khanh/ai-invoice-billing

# 1. Backend (nạp .env: JWT_SECRET, DATABASE_PATH, AUTO_VERIFY_EMAIL)
set -a && . ./.env && set +a && export PORT=8005
nohup ./backend-rs/target/release/quanh-ta-backend > /tmp/quanh-ta-rs.log 2>&1 & disown

# 2. Frontend bản PRODUCTION (vite preview :5175) + proxy API tới :8005
#    Quan trọng: dùng preview, KHÔNG dùng `vite dev` — bản dev có IS_DEV=true
#    nên lộ nút "đặt lại mật khẩu bằng username" (ai có link cũng đổi được mật khẩu).
cd frontend
nohup npx vite preview --port 5175 --host 127.0.0.1 > /tmp/vite-preview.log 2>&1 & disown
cd ..

# 3. Tunnel
nohup /tmp/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:5175 > /tmp/cloudflared.log 2>&1 & disown

# 4. Lấy link mới
sleep 8 && grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log | head -1
```

Kiểm tra nhanh:
```bash
curl -s http://127.0.0.1:8005/health     # {"status":"ok"...}
curl -s <link>/health                     # {"status":"ok"...}  (từ internet)
```

## Tài khoản & email (QUAN TRỌNG)

Hiện tại `.env` có:
```
OPEN_REGISTRATION=1     # ai có link cũng tự đăng ký được
AUTO_VERIFY_EMAIL=1     # tự xác minh → đăng nhập ngay, KHÔNG cần email
```

Điều này **chỉ phù hợp để thử nghiệm**. Ai có link cũng tạo được tài khoản và dùng app,
vì email không được kiểm chứng thật.

Khi chuyển sang production thật (cho người lạ dùng):
1. Đăng ký **Resend** free tier (https://resend.com) → lấy API key dạng `re_xxx`.
2. Trong `.env`:
   ```
   EMAIL_SMTP_HOST=smtp.resend.com
   EMAIL_SMTP_PORT=587
   EMAIL_SMTP_USER=resend
   EMAIL_SMTP_PASS=re_xxx...
   AUTO_VERIFY_EMAIL=0     # bật lại xác minh email thật
   ```
3. Đăng ký **Resend** free tier (https://resend.com) → lấy API key dạng `re_xxx`.
4. Trong `.env`:
   ```
   EMAIL_SMTP_HOST=smtp.resend.com
   EMAIL_SMTP_PORT=587
   EMAIL_SMTP_USER=resend
   EMAIL_SMTP_PASS=re_xxx...
   EMAIL_FROM=onboarding@resend.dev   # hoặc domain đã xác minh
   AUTO_VERIFY_EMAIL=0     # bật lại xác minh email thật
   ```
5. Restart backend. Giờ user mới phải xác minh email trước khi đăng nhập — đúng chuẩn production.

**Quên mật khẩu:** email gửi link `/auth/reset-password/confirm?token=...` → backend redirect về app (`/?reset_token=...`) → app tự mở màn "Đặt lại mật khẩu" với mã điền sẵn. Token dùng một lần, hết hạn 1 giờ.

## Dữ liệu

DB đang dùng: `data/spends.db` (1 user thật "Khánh", 4 lần chi, 4 quán).
Backup các bản cũ: `backups/20261117-150823/`.

## Giới hạn của cách chạy thử này

- Máy phải bật 24/7 — tắt máy là app chết.
- Link tunnel đổi mỗi lần restart → phải gửi link mới cho người dùng.
- Vite dev chạy chế độ development (có `/@vite/client`), hơi chậm hơn bản build — nhưng chức năng đầy đủ.
- Không có tên miền cố định, không phù hợp làm địa chỉ lâu dài.

👉 Khi muốn ổn định lâu dài: mua VPS (~150k/tháng) + domain + làm theo `scripts/deploy.sh`.
