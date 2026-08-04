"""Lớp cơ sở SQLite dùng chung.
':memory:' 1 connection cố định (check_same_thread=False cho TestClient);
file DB mở-đóng mỗi lần, tự commit."""
import sqlite3
from contextlib import contextmanager


class SQLiteRepo:
    def __init__(self, db_path: str = "invoices.db"):
        self.db_path = db_path
        self._mem_conn = None
        self._init_db()

    @contextmanager
    def _connect(self):
        if self.db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
                self._ensure_schema(self._mem_conn)
            yield self._mem_conn
            self._mem_conn.commit()
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            self._ensure_schema(conn)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_db(self):
        """Tạo schema cho DB file."""
        with self._connect():
            pass

    def _ensure_schema(self, conn: sqlite3.Connection):
        """Subclass định nghĩa schema riêng."""
        raise NotImplementedError