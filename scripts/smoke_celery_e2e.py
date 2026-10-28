"""Smoke test E2E cho luồng extraction bất đồng bộ thật (Celery + Redis + uvicorn).

Mô phỏng đúng deploy production: API chạy WORKER_ENABLED=1 đẩy task qua Redis,
celery worker riêng nhận và xử lý, client polling /invoices/{id} cho tới khi xong.

Chạy (cần Redis sẵn sàng — REDIS_URL mặc định redis://127.0.0.1:6390/0):
    python scripts/smoke_celery_e2e.py

Thoát 0 = PASS. File DB/storage tạo trong /tmp, không đụng dev data.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time

import httpx

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6390/0")
APP_PORT = int(os.getenv("SMOKE_APP_PORT", "8123"))
BASE = f"http://127.0.0.1:{APP_PORT}"

GTGT = (
    "HÓA ĐƠN GIÁ TRỊ GIA TĂNG\n"
    "Số hóa đơn: 00012345\n"
    "Người bán: CÔNG TY TNHH ABC\n"
    "Cộng tiền hàng hóa, dịch vụ: 29,000,000\n"
    "Chiết khấu thương mại: 1,000,000\n"
    "Thuế GTGT: 2,900,000\n"
    "Tổng cộng tiền thanh toán: 30,900,000\n"
    "Ngày 04/08/2026\n"
)


def _wait_health(client: httpx.Client, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.get(f"{BASE}/health").status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit("FAIL: API không lên được /health")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="smoke-celery-")
    env = {
        **os.environ,
        "APP_ENV": "development",
        "OPEN_REGISTRATION": "1",
        "JWT_SECRET": "smoke-test-secret-not-for-prod-32bytes",
        "DATABASE_PATH": os.path.join(tmp, "invoices.db"),
        "STORAGE_DIR": os.path.join(tmp, "storage"),
        "REDIS_URL": REDIS_URL,
        "CELERY_RESULT_BACKEND": REDIS_URL.replace("/0", "/1"),
        "WORKER_ENABLED": "1",
        "VAPID_PUBLIC_KEY": "",
        "VAPID_PRIVATE_KEY": "",
    }
    procs: list[subprocess.Popen] = []
    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "src.app:app", "--port", str(APP_PORT), "--log-level", "warning"],
            env=env, cwd=_repo_root(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        ))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "celery", "-A", "src.workers.celery_app", "worker",
             "-l", "warning", "-Q", "extraction,default", "-c", "1"],
            env=env, cwd=_repo_root(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        ))

        client = httpx.Client(timeout=10)
        _wait_health(client)

        # Đăng ký + xác minh (đứng ngoài app process nên đụng thẳng DB tạm)
        r = client.post(f"{BASE}/auth/register", json={"username": "smokeu", "password": "smoke12345"})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        os.environ["DATABASE_PATH"] = env["DATABASE_PATH"]
        sys.path.insert(0, _repo_root())
        from src.store.sql_repository import Database, UserRepository

        users = UserRepository(Database(env["DATABASE_PATH"]))
        users.set_verified(users.get_by_username("smokeu").id, True)
        headers = {"Authorization": f"Bearer {token}"}

        # Upload → 202 + processing ngay (chưa trích xuất)
        r = client.post(f"{BASE}/upload", files={"file": ("hoadon.txt", GTGT.encode(), "text/plain")}, headers=headers)
        assert r.status_code == 202, f"upload: {r.status_code} {r.text}"
        inv = r.json()
        assert inv["job_status"] == "processing", inv
        assert inv["total"] == 0.0, inv
        print(f"[smoke] 202 + processing — invoice_id={inv['id']}, task đã vào queue Redis")

        # Poll cho tới khi worker xử lý xong
        deadline = time.time() + 60
        fresh = None
        while time.time() < deadline:
            fresh = client.get(f"{BASE}/invoices/{inv['id']}", headers=headers).json()
            if fresh["job_status"] not in ("processing", "pending"):
                break
            time.sleep(1.0)
        assert fresh["job_status"] == "review", fresh
        assert fresh["invoice_number"] == "00012345", fresh
        assert fresh["vendor"] == "CÔNG TY TNHH ABC", fresh
        assert fresh["total"] == 30900000.0, fresh
        print(f"[smoke] worker xử lý xong: job_status=review, invoice_number=00012345, total=30,900,000")
        print("PASS: luồng async thật (API -> Redis -> Celery worker -> DB -> polling) hoạt động")
        return 0
    except AssertionError as e:
        print(f"FAIL: {e}")
        return 1
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


if __name__ == "__main__":
    sys.exit(main())
