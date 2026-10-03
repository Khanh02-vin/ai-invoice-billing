#!/usr/bin/env python3
"""Sinh cặp VAPID key cho Web Push (thông báo native PWA).

Chạy:  python3 scripts/gen_vapid_keys.py
Rồi copy nguyên 3 dòng in ra vào .env, và restart backend:

    VAPID_PUBLIC_KEY=BEl...   (base64url, frontend dùng để subscribe)
    VAPID_PRIVATE_KEY=./vapid_private.pem   (file PEM, đã gitignore)
    VAPID_SUBJECT=mailto:ban@example.com

Chạy lại script khi file PEM đã tồn tại -> đọc lại key cũ (không phá
subscription đang hoạt động trên các thiết bị).
"""
import base64
import sys
from pathlib import Path

try:
    from py_vapid import Vapid02
    from cryptography.hazmat.primitives import serialization
except ImportError:
    sys.exit("Thiếu thư viện — chạy: pip install pywebpush (kéo theo py_vapid + cryptography)")

KEY_PATH = Path(__file__).resolve().parent.parent / "vapid_private.pem"


def public_key_b64(vapid) -> str:
    """Base64url của uncompressed EC point — đúng định dạng applicationServerKey."""
    raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def main() -> None:
    if KEY_PATH.exists():
        vapid = Vapid02.from_file(str(KEY_PATH))
        print(f"# Dùng lại key có sẵn: {KEY_PATH}", file=sys.stderr)
    else:
        vapid = Vapid02()
        vapid.generate_keys()
        vapid.save_key(str(KEY_PATH))
        print(f"# Đã tạo key mới: {KEY_PATH}", file=sys.stderr)

    print(f"VAPID_PUBLIC_KEY={public_key_b64(vapid)}")
    print(f"VAPID_PRIVATE_KEY=./{KEY_PATH.name}")
    print("VAPID_SUBJECT=mailto:admin@localhost")


if __name__ == "__main__":
    main()
