"""MFA (TOTP) tiêu chuẩn — stdlib only, không dùng pyotp.

Triển khai RFC 6238 (TOTP) và RFC 4648 (base32).
- Secret: base32 (A-Z2-7), random bytes.
- TOTP: 6 chữ số, cửa sổ 30 giây, SHA1.
- Backup codes: mã phục hồi dùng 1 lần."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import List


def generate_mfa_secret(length: int = 32) -> str:
    """Tạo secret base32 ngẫu nhiên (mặc định 32 ký tự = 160 bits)."""
    # base32 mỗi ký tự mã hóa 5 bits. Sinh đủ rồi cắt.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _base32_decode(secret: str) -> bytes:
    """Giải mã base32, bỏ padding, chấp nhận lowercase."""
    s = secret.upper().replace(" ", "").replace("-", "")
    # Thêm padding nếu thiếu.
    pad = (-len(s)) % 8
    s += "=" * pad
    return base64.b32decode(s)


def _dynamic_truncation(hmac_digest: bytes) -> int:
    """Dynamic truncation theo RFC 4226/6238."""
    offset = hmac_digest[-1] & 0x0F
    struct_bytes = hmac_digest[offset : offset + 4]
    (value,) = struct.unpack(">I", struct_bytes)
    return value & 0x7FFFFFFF


def get_totp_token(secret: str, drift_steps: int = 0) -> str:
    """Tạo TOTP 6 chữ số tại thời điểm hiện tại + drift_steps * 30s."""
    key = _base32_decode(secret)
    counter = int(time.time()) // 30 + drift_steps
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    otp = _dynamic_truncation(h) % 1_000_000
    return f"{otp:06d}"


def verify_totp(secret: str, token: str, window: int = 1) -> bool:
    """Xác minh TOTP trong cửa sổ +/- window bước (mỗi bước 30s)."""
    if not token or len(token) != 6 or not token.isdigit():
        return False
    for step in range(-window, window + 1):
        if secrets.compare_digest(get_totp_token(secret, drift_steps=step), token):
            return True
    return False


def generate_backup_codes(n: int = 8) -> List[str]:
    """Tạo n backup code dùng 1 lần, định dạng xxxx-xxxx."""
    codes = []
    for _ in range(n):
        # 8 bytes hex -> 16 ký tự, chia 4-4-4-4.
        raw = secrets.token_hex(8)
        code = f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"
        codes.append(code)
    return codes


def get_totp_uri(secret: str, issuer: str, account: str) -> str:
    """Tạu URI otpauth:// để hiển thị QR cho Google Authenticator/Authy."""
    label = f"{issuer}:{account}"
    params = f"secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    return f"otpauth://totp/{label}?{params}"
