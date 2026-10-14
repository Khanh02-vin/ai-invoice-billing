"""Compatibility helpers for SQLModel and legacy repositories in tests."""

from src.store.sql_repository import (
    Database,
    InvoiceRepository as SQLInvoiceRepository,
    TransactionRepository as SQLTransactionRepository,
    UserRepository as SQLUserRepository,
    PushSubscriptionRepository as SQLPushSubscriptionRepository,
)
from src.store.repository import InvoiceRepository
from src.store.users import UserRepository
from src.store.push import PushSubscriptionRepository as LegacyPushSubscriptionRepository
from src.models import Invoice as SQLInvoice

__all__ = [
    "Database", "InvoiceRepository", "UserRepository", "TransactionRepository",
    "PushSubscriptionRepository", "create_test_database", "create_test_invoice_repo",
    "create_test_user_repo", "create_test_tx_repo", "create_test_push_repo",
    "TestInvoiceRepository", "TestUserRepository", "TestTransactionRepository",
    "TestPushSubscriptionRepository",
]

TransactionRepository = SQLTransactionRepository
PushSubscriptionRepository = SQLPushSubscriptionRepository


def create_test_database() -> Database:
    db = Database(":memory:")
    db.create_tables()
    return db


def create_test_invoice_repo() -> SQLInvoiceRepository:
    return SQLInvoiceRepository(create_test_database())


def create_test_user_repo() -> SQLUserRepository:
    return SQLUserRepository(create_test_database())


def create_test_tx_repo() -> SQLTransactionRepository:
    return SQLTransactionRepository(create_test_database())


def create_test_push_repo() -> SQLPushSubscriptionRepository:
    return SQLPushSubscriptionRepository(create_test_database())


class TestInvoiceRepository(InvoiceRepository):
    __test__ = False

    def __init__(self, db_path: str = ":memory:"):
        super().__init__(db_path)

    def _to_model(self, invoice):
        if not invoice.created_at:
            from datetime import datetime, timezone

            invoice.created_at = datetime.now(timezone.utc)
        if not invoice.id:
            import uuid

            invoice.id = uuid.uuid4().hex[:16]
        return invoice

    def upsert(self, invoice):
        return super().upsert(self._to_model(invoice))


class TestUserRepository(UserRepository):
    __test__ = False

    def __init__(self, db_path: str = ":memory:"):
        super().__init__(db_path)


class TestTransactionRepository(SQLTransactionRepository):
    __test__ = False

    def __init__(self, db_path: str = ":memory:"):
        db = Database(db_path)
        db.create_tables()
        super().__init__(db)

    def create(self, tx):
        with self.db.get_session() as session:
            session.add(tx)
            session.commit()
            session.refresh(tx)
        return tx


class TestPushSubscriptionRepository(LegacyPushSubscriptionRepository):
    __test__ = False

    def __init__(self, db_path: str = ":memory:"):
        super().__init__(db_path)
