"""Test utilities for SQLModel-based repositories."""

import tempfile
from pathlib import Path

from src.store.sql_repository import (
    Database, InvoiceRepository, UserRepository, TransactionRepository,
    PushSubscriptionRepository, init_database
)


def create_test_database() -> Database:
    """Create an in-memory database for testing."""
    db = Database(":memory:")
    db.create_tables()
    return db


def create_test_invoice_repo() -> InvoiceRepository:
    """Create an InvoiceRepository with in-memory database."""
    db = create_test_database()
    return InvoiceRepository(db)


def create_test_user_repo() -> UserRepository:
    """Create a UserRepository with in-memory database."""
    db = create_test_database()
    return UserRepository(db)


def create_test_tx_repo() -> TransactionRepository:
    """Create a TransactionRepository with in-memory database."""
    db = create_test_database()
    return TransactionRepository(db)


def create_test_push_repo() -> PushSubscriptionRepository:
    """Create a PushSubscriptionRepository with in-memory database."""
    db = create_test_database()
    return PushSubscriptionRepository(db)


# For backward compatibility with tests expecting db_path parameter
class TestInvoiceRepository(InvoiceRepository):
    """Wrapper that accepts db_path like the old repository."""
    
    def __init__(self, db_path: str = ":memory:"):
        if db_path == ":memory:":
            db = Database(":memory:")
        else:
            db = Database(db_path)
        db.create_tables()
        super().__init__(db)


class TestUserRepository(UserRepository):
    """Wrapper that accepts db_path like the old repository."""
    
    def __init__(self, db_path: str = ":memory:"):
        if db_path == ":memory:":
            db = Database(":memory:")
        else:
            db = Database(db_path)
        db.create_tables()
        super().__init__(db)


class TestTransactionRepository(TransactionRepository):
    """Wrapper that accepts db_path like the old repository."""
    
    def __init__(self, db_path: str = ":memory:"):
        if db_path == ":memory:":
            db = Database(":memory:")
        else:
            db = Database(db_path)
        db.create_tables()
        super().__init__(db)