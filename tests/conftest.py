"""Pytest conftest — test KHÔNG BAO GIỜ chạm DB dev (invoices.db ở repo root).

Lý do: SQLiteRepo mở/tạo file DB ngay lúc construct, nên chỉ cần import src.app
là file invoices.db (cwd) được tạo. Các fixture cũ xoá `invoices.db` sau test để
dọn — nhưng đang chạy dev thì việc đó xoá luôn DB thật (server restart thấy DB
trống). Thay vào đó, trỏ mọi đường dẫn mặc định về thư mục tạm của phiên test.

Env phải set ở import-time (trước khi test module import src) để repo singleton
cấp module (auth_ext.user_repo, orgs/search/billing) cũng trỏ vào tmp.
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="invoice-tests-")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "invoices.db")
os.environ["STORAGE_DIR"] = os.path.join(_TMP, "storage")

import pytest


@pytest.fixture(autouse=True)
def safe_db(tmp_path, monkeypatch):
    """Mỗi test một DB tạm riêng — không đụng file thật; pytest tự xoá tmp_path."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("APP_URL", "http://testserver")
    yield
