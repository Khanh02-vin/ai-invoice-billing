#!/usr/bin/env bash
# Xoay vòng secret + backup trước khi deploy.
#
#   ./scripts/rotate-secrets.sh            # xem trước, không ghi gì
#   ./scripts/rotate-secrets.sh --apply    # thực sự làm
#
# Việc script làm được:
#   1. Backup .env* (kèm quyền 600) vào backups/ — đã gitignore.
#   2. Sinh JWT_SECRET mới (64 ký tự) cho file env được chọn.
#   3. Thu hồi toàn bộ refresh token đang sống trong DB (nếu có).
#
# Việc script KHÔNG làm được (cần bạn tự làm, script sẽ nhắc):
#   - EMAIL_SMTP_PASS: đây là App Password do Google cấp. Chỉ bạn đăng nhập
#     https://myaccount.google.com/apppasswords mới tạo được. Script không bịa
#     giá trị giả vì mail sẽ chết âm thầm.
#   - IMAP_CREDENTIAL_KEY: code hiện không đọc biến này (di sản sản phẩm cũ).
set -euo pipefail
cd "$(dirname "$0")/.."

APPLY=0
ENV_FILE=".env"
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --env=*) ENV_FILE="${arg#--env=}" ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Tham số lạ: $arg (xem --help)"; exit 1 ;;
  esac
done

[ -f "$ENV_FILE" ] || { echo "Không thấy $ENV_FILE"; exit 1; }

echo "== File env: $ENV_FILE"
echo "== Chế độ: $([ "$APPLY" = 1 ] && echo THỰC THI || echo 'XEM TRƯỚC (thêm --apply)')"

# 1. Backup trước khi sửa bất cứ thứ gì
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="backups/$STAMP"
if [ "$APPLY" = 1 ]; then
  # Backups chứa bản sao .env (quyền 600) và DB người dùng thật. Nếu thư mục
  # không bị gitignore thì phải dừng — thà không backup còn hơn đẩy secret lên git.
  if ! git check-ignore -q "$BACKUP_DIR" 2>/dev/null; then
    echo "LỖI: '$BACKUP_DIR' không bị gitignore. Thêm 'backups/' vào .gitignore trước." >&2
    exit 1
  fi
  mkdir -p "$BACKUP_DIR"
  # Tên file phải giữ được thư mục gốc: cả '.env' và 'backend-rs/.env' đều tên
  # là '.env' — dùng basename sẽ khiến bản sau ghi đè bản trước.
  [ -f .env ] && install -m 600 .env "$BACKUP_DIR/root.env"
  [ -f .env.production ] && install -m 600 .env.production "$BACKUP_DIR/root.env.production"
  [ -f backend-rs/.env ] && install -m 600 backend-rs/.env "$BACKUP_DIR/backend-rs.env"
  # Backup DB nếu có: mất DB là mất dữ liệu thật, không thể sinh lại.
  for db in data/spends.db data/prod/invoices.db; do
    [ -f "$db" ] && { cp "$db" "$BACKUP_DIR/$(basename "$db")"; echo "  backup $db"; }
  done
  echo "  backup env -> $BACKUP_DIR (quyền 600)"
else
  echo "  (sẽ) backup .env* + DB -> backups/$STAMP/"
fi

# 2. Xoay JWT_SECRET — mọi access/refresh token cũ mất hiệu lực
NEW_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
if [ "$APPLY" = 1 ]; then
  ENV_FILE="$ENV_FILE" NEW_SECRET="$NEW_SECRET" python3 -I - <<'PY'
import os, re, pathlib
p = pathlib.Path(os.environ["ENV_FILE"])
text = p.read_text()
new = os.environ["NEW_SECRET"]
text2, n = re.subn(r'^JWT_SECRET=.*$', "JWT_SECRET=" + new, text, count=1, flags=re.M)
if n == 0:  # chưa có dòng nào thì thêm
    text2 = text + ("\n" if not text.endswith("\n") else "") + "JWT_SECRET=" + new + "\n"
p.write_text(text2)
print(f"  JWT_SECRET mới (len={len(new)}) đã ghi vào {p}")
PY
else
  echo "  (sẽ) sinh JWT_SECRET mới (len=64) và ghi vào $ENV_FILE"
fi

# 3. Thu hồi refresh token — secret mới đã làm chúng vô dụng, đánh dấu cho sạch
DB_PATH=$(grep -E '^DATABASE_PATH=' "$ENV_FILE" | cut -d= -f2- || true)
DB_PATH="${DB_PATH:-data/spends.db}"
if [ -f "$DB_PATH" ]; then
  LIVE=$(python3 -I -c "
import sqlite3,sys
try:
    c=sqlite3.connect(sys.argv[1])
    print(c.execute('SELECT COUNT(*) FROM refresh_tokens WHERE revoked=0').fetchone()[0])
except Exception:
    print(0)
" "$DB_PATH")
  echo "== DB: $DB_PATH — $LIVE refresh token đang sống"
  if [ "$APPLY" = 1 ] && [ "$LIVE" -gt 0 ]; then
    python3 -I -c "
import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
c.execute('UPDATE refresh_tokens SET revoked=1 WHERE revoked=0')
c.commit()
print('  đã thu hồi', c.total_changes, 'token')
" "$DB_PATH"
  fi
else
  echo "== DB: $DB_PATH không tồn tại (bỏ qua bước thu hồi)"
fi

# 4. Nhắc việc không thể tự động hoá
cat <<'EOF'

== CÒN LẠI BẠN PHẢI TỰ LÀM ==
1. EMAIL_SMTP_PASS — App Password của Google:
   - Vào https://myaccount.google.com/apppasswords (cần bật 2FA trước).
   - Tạo app password mới, thu hồi cái cũ, rồi dán vào .env:
       EMAIL_SMTP_PASS=<mật-khẩu-16-ký-tự-mới>
   - Không có cách nào tự sinh: giá trị phải do Google cấp.
2. IMAP_CREDENTIAL_KEY — không cần xoay: code hiện không đọc biến này.
3. Sau khi xong, khởi động lại backend:
       docker compose up -d --build
   Test gửi mail: đăng ký tài khoản mới và xem có nhận email xác minh.
EOF

[ "$APPLY" = 1 ] || { echo; echo "Chưa ghi gì. Chạy lại với --apply để thực thi."; }
