"""IMAP poller — đọc email thông báo giao dịch ngân hàng/ví -> /payments/ingest.

Cách hoạt động:
- Thread nền, mỗi IMAP_INTERVAL_SECONDS (mặc định 60s) quét IMAP_FOLDER.
- Mỗi email: subject + body text -> cùng parser với SMS (src/payments/parser.py).
- Gắn email -> user qua IMAP_USER_MAP ("from1@x.com,username2"); nếu trống,
  mọi email gán cho MỌI user verified (chỉ dùng khi inbox là của chính người dùng).
- Dedupe tự nhiên theo (user_id, external_ref) — email đọc lại không tạo bản trùng.

Không bật khi thiếu IMAP_HOST/IMAP_USER — app chạy bình thường không có poller.
Chỉ stdlib (imaplib + email) — không thêm dependency.
"""
from __future__ import annotations

import email
import imaplib
import logging
import threading
from email.header import decode_header
from email.message import Message
from typing import Callable, Dict, List, Optional

from ..config import Settings, get_settings
from ..domain.payments import PaymentEvent, TxDirection
from ..payments.notify import send_receipt_reminder
from ..payments.parser import parse_payment_text
from ..payments.service import ingest
from ..store.transactions import TransactionRepository
from ..store.users import UserRepository

logger = logging.getLogger("invoice.imap")

# Nhãn nguồn để truy vết email đến từ đâu
SOURCE = "email"


def decode_mime_header(value: str) -> str:
    """Decode Subject/From RFC 2047 (=?UTF-8?B?...?=)."""
    if not value:
        return ""
    try:
        parts = []
        for chunk, charset in decode_header(value):
            if isinstance(chunk, bytes):
                parts.append(chunk.decode(charset or "utf-8", errors="replace"))
            else:
                parts.append(chunk)
        return "".join(parts)
    except Exception:
        return value


def get_body_text(msg: Message) -> str:
    """Lấy body text/plain (decode quoted-printable/base64); fallback HTML strip tag."""
    plain: Optional[str] = None
    html: Optional[str] = None
    for part in msg.walk():
        if part.get_content_maintype() != "text":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        if part.get_content_subtype() == "plain" and plain is None:
            plain = text
        elif part.get_content_subtype() == "html" and html is None:
            html = text
    if plain:
        return plain
    if html:
        import re
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def build_event_text(msg: Message) -> str:
    """Gộp Subject + body thành 1 text để parse (subject thường chứa số tiền)."""
    subject = decode_mime_header(msg.get("Subject", ""))
    body = get_body_text(msg)
    return f"{subject}\n{body}".strip()


def parse_recipients(raw: str) -> Dict[str, str]:
    """Parse IMAP_USER_MAP "email1,user2" -> {email.lower(): username}."""
    mapping: Dict[str, str] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" in pair:
            addr, username = pair.split("=", 1)
        elif ":" in pair:
            addr, username = pair.split(":", 1)
        else:
            addr, username = pair, pair
        mapping[addr.strip().lower()] = username.strip()
    return mapping


def resolve_user_ids(msg: Message, user_repo: UserRepository, user_map: Dict[str, str]) -> List[str]:
    """Quyết định email này thuộc về user nào.

    Thứ tự: IMAP_USER_MAP (nếu cấu hình — chỉ match map, không broadcast)
    -> cột users.email khớp sender -> username là email khớp sender
    -> broadcast mọi user verified (inbox cá nhân / single-user).
    """
    headers = " ".join(
        (msg.get(h) or "") for h in ("From", "X-Original-To", "Delivered-To")
    ).lower()

    if user_map:
        ids: List[str] = []
        for known, username in user_map.items():
            if known in headers:
                user = user_repo.get_by_username(username)
                if user and user.verified and user.id not in ids:
                    ids.append(user.id)
        return ids

    users = user_repo.list_verified()
    ids = [u.id for u in users if u.email and u.email.lower() in headers]
    if ids:
        return ids
    ids = [u.id for u in users if "@" in u.username and u.username.lower() in headers]
    if ids:
        return ids
    return [u.id for u in users]


class ImapPoller:
    """Poll IMAP 1 lần mỗi interval — start()/stop() cho app lifecycle."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        user_repo: Optional[UserRepository] = None,
        tx_repo: Optional[TransactionRepository] = None,
        invoice_repo_getter: Optional[Callable] = None,
    ):
        s = settings or get_settings()
        self._settings = s
        self._user_repo = user_repo or UserRepository(s.database_path)
        self._tx_repo = tx_repo or TransactionRepository(s.database_path)
        # Invoice repo lấy qua callable để test patch được (app_module.repo)
        self._invoice_getter = invoice_repo_getter
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def enabled(self) -> bool:
        return self._settings.imap_enabled

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="imap-poller", daemon=True)
        self._thread.start()
        logger.info(
            "IMAP poller started host=%s folder=%s interval=%ss",
            self._settings.imap_host,
            self._settings.imap_folder,
            self._settings.imap_interval_seconds,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        # Chờ nhịp đầu tiên để dev test app không bị block kết nối IMAP
        if self._stop.wait(timeout=1):
            return
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("IMAP poll failed (sẽ thử lại ở chu kỳ sau)")
            if self._stop.wait(timeout=max(5, self._settings.imap_interval_seconds)):
                return

    def poll_once(self, fetch_fn: Optional[Callable] = None) -> int:
        """Quét 1 lần. Trả về số giao dịch đã ingest.

        fetch_fn -> ([(num, raw_bytes)], mark_seen(nums)) — inject được cho test.
        Email xử lý xong (kể cả không phải giao dịch) -> đánh dấu \\Seen;
        email gặp exception bất ngờ -> GIỮ LẠI (UNSEEN) cho chu kỳ sau.
        """
        fetch = fetch_fn or self._fetch_unseen
        messages, mark_seen = fetch()
        if not messages:
            return 0
        user_map = parse_recipients(self._settings.imap_user_map)
        count = 0
        handled: list = []
        for num, raw in messages:
            try:
                msg = email.message_from_bytes(raw)
                event = parse_payment_text(build_event_text(msg), source=SOURCE)
                if event is None or event.direction == TxDirection.CREDIT:
                    handled.append(num)
                    continue
                # message-id làm external_ref -> dedupe cả khi mark_seen thất bại
                ref = (msg.get("Message-ID") or "").strip() or raw[:64].decode("utf-8", "replace")
                count += self._ingest_for_recipients(msg, event, ref, user_map)
                handled.append(num)
            except Exception:
                logger.exception("IMAP: xử lý 1 email thất bại — giữ lại email cho chu kỳ sau")
        if handled and mark_seen:
            try:
                mark_seen(handled)
            except Exception:
                logger.warning("IMAP: không đánh dấu seen được (email sẽ bị đọc lại — dedupe chặn trùng)")
        return count

    def _ingest_for_recipients(
        self, msg: Message, event: PaymentEvent, ref: str, user_map: Dict[str, str]
    ) -> int:
        user_ids = resolve_user_ids(msg, self._user_repo, user_map)
        if not user_ids:
            return 0
        raw_text = build_event_text(msg)
        invoice_repo = self._invoice_getter() if self._invoice_getter else None
        if invoice_repo is None:
            from .. import app as app_module
            invoice_repo = app_module.repo
        done = 0
        errors = 0
        for uid in user_ids:
            try:
                tx, invoice, dup = ingest(
                    self._tx_repo, invoice_repo, uid, event, raw_text, external_ref=ref
                )
                done += 1
                if not dup and invoice is None:
                    # Chưa ghép được hóa đơn -> nhắc user chụp bill (best-effort)
                    user = self._user_repo.get(uid)
                    if user:
                        send_receipt_reminder(user, tx)
            except Exception:
                errors += 1
                logger.exception("IMAP: ingest failed user=%s", uid)
        if errors:
            # Báo fail để poll_once GIỮ email (UNSEEN) — user fail sẽ retry chu kỳ sau,
            # user đã thành công được dedupe bảo vệ.
            raise RuntimeError(f"IMAP ingest failed for {errors} user(s)")
        return done

    def _fetch_unseen(self):
        """Kết nối IMAP thật -> ([(num, raw message)], mark_seen(nums))."""
        s = self._settings
        client = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, timeout=30)
        try:
            client.login(s.imap_user, s.imap_pass)
            client.select(s.imap_folder)
            status, data = client.search(None, "UNSEEN")
            if status != "OK":
                return [], None
            nums = (data[0] or b"").split()[:50]  # giới hạn mỗi vòng
            messages: List[tuple] = []
            for num in nums:
                status, msg_data = client.fetch(num, "(RFC822)")
                if status == "OK" and msg_data and msg_data[0]:
                    messages.append((num, msg_data[0][1]))

            def mark_seen(nums_to_mark):
                for num in nums_to_mark:
                    client.store(num, "+FLAGS", "\\Seen")

            return messages, mark_seen
        finally:
            try:
                client.logout()
            except Exception:
                pass
