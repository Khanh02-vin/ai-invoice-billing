#!/usr/bin/env bash
# Smoke-compare hai backend đã deploy (Python :8000 vs Rust :8001) trên các
# endpoint public + contract lỗi — không phụ thuộc user/DB nên chạy được ngay
# sau `docker compose up`.
#
# Dùng:
#   scripts/compare_backends.sh [PY_URL] [RS_URL]
#   scripts/compare_backends.sh http://localhost:8000 http://localhost:8001
#
# So sánh đầy đủ (có user/DB) xem scripts trong phiên làm việc dev — phần parity
# 73 case dùng DB riêng cho từng phía.
set -u

PY_URL="${1:-http://localhost:8000}"
RS_URL="${2:-http://localhost:8001}"

scrub() { jq -S 'walk(if type == "object" then del(.request_id) else . end)'; }

qr1='00020101021138400010A00000072701220006693543010812345678530370454061250005802VN5909QUAN TEST6304ABCD'

run_case() {
  local name="$1" method="$2" path="$3" token="$4" data="$5"
  for side in py rs; do
    local url="$PY_URL"
    [ "$side" = rs ] && url="$RS_URL"
    local args=(-s -X "$method" "$url$path")
    [ -n "$token" ] && args+=(-H "Authorization: Bearer $token")
    [ -n "$data" ] && args+=(-H 'Content-Type: application/json' -d "$data")
    curl "${args[@]}" | scrub > "/tmp/cmp-$side-$name.json"
  done
  if diff -q "/tmp/cmp-py-$name.json" "/tmp/cmp-rs-$name.json" >/dev/null; then
    echo "PASS  $name"
  else
    echo "FAIL  $name"
    diff "/tmp/cmp-py-$name.json" "/tmp/cmp-rs-$name.json" | head -10
  fi
}

echo "== so sánh $PY_URL (Python) vs $RS_URL (Rust) =="
run_case health        '.'   GET  /health '' ''
run_case ready         '.'   GET  /ready  '' ''
run_case google_status '.'   GET  /auth/google/status '' ''
run_case notfound_404  '.'   GET  /khong-ton-tai '' ''
run_case method_405    '.'   POST /health '' ''
run_case no_auth_401   '.'   GET  /spends '' ''
run_case parse_qr_vietqr  '.' POST /spends/parse-qr '' "{\"text\":\"$qr1\"}"
run_case parse_qr_num     '.' POST /spends/parse-qr '' '{"text":"125.000đ"}'
run_case parse_qr_json    '.' POST /spends/parse-qr '' '{"text":"{\"amount\": 45000, \"soluong\": 3}"}'
run_case parse_qr_empty   '.' POST /spends/parse-qr '' '{}'
run_case register_validation '.' POST /auth/register '' '{"username":"ab"}'

# Security headers + X-Request-ID echo
hdr_check() {
  local url="$1" tag="$2"
  curl -s -D - -o /dev/null -H 'X-Request-ID: cmp-rid-001' "$url/health" \
    | grep -iE "^(x-content-type-options|x-frame-options|x-request-id|permissions-policy):" \
    | tr -d '\r' | sort > "/tmp/cmp-hdr-$tag.txt"
}
hdr_check "$PY_URL" py
hdr_check "$RS_URL" rs
if diff -q /tmp/cmp-hdr-py.txt /tmp/cmp-hdr-rs.txt >/dev/null; then
  echo "PASS  security_headers"
else
  echo "FAIL  security_headers"; diff /tmp/cmp-hdr-py.txt /tmp/cmp-hdr-rs.txt
fi

echo "== xong =="
