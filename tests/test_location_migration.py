from src.models import Invoice, PaymentTransaction, Purchase, User
from src.store.sql_repository import (
    Database,
    _backfill_location_data,
    _ensure_invoice_mime_column,
    _ensure_location_columns,
)


def test_location_backfill_preserves_zero_coordinates_and_is_idempotent(tmp_path):
    db = Database(str(tmp_path / "location-migration.db"))
    db.create_tables()
    with db.get_session() as session:
        session.add(User(id="migration-user", username="migration-user", password_hash="x" * 60))
        session.commit()
    with db.get_session() as session:
        session.add(PaymentTransaction(id="migration-tx", user_id="migration-user", amount=1))
        session.add(Invoice(id="migration-invoice", user_id="migration-user"))
        session.add(Purchase(id="migration-purchase", user_id="migration-user"))
        session.commit()
    with db.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE payment_transactions SET location_lat=0, location_lon=0, "
            "location_captured_at='2026-01-02T03:04:05Z'"
        )
        conn.exec_driver_sql(
            "UPDATE invoices SET location_lat=10.5, location_lon=106.2, "
            "location_name='Merchant'"
        )
        conn.exec_driver_sql(
            "UPDATE purchases SET location_lat=0, location_lon=106.2, "
            "location_captured_at=NULL"
        )

    _ensure_location_columns(db)
    _backfill_location_data(db)
    _backfill_location_data(db)

    with db.engine.connect() as conn:
        tx = conn.exec_driver_sql(
            "SELECT payment_lat, payment_lon, payment_captured_at, merchant_lat FROM payment_transactions"
        ).first()
        invoice = conn.exec_driver_sql(
            "SELECT merchant_lat, merchant_lon, merchant_name, payment_lat FROM invoices"
        ).first()
        purchase = conn.exec_driver_sql(
            "SELECT merchant_lat, merchant_lon, payment_lat FROM purchases"
        ).first()
    assert tuple(tx) == (0.0, 0.0, "2026-01-02T03:04:05Z", None)
    assert tuple(invoice) == (10.5, 106.2, "Merchant", None)
    assert tuple(purchase) == (0.0, 106.2, None)


def test_location_backfill_skips_tables_without_legacy_coordinates(tmp_path):
    db = Database(str(tmp_path / "missing-legacy-location.db"))
    db.create_tables()
    with db.engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE invoices DROP COLUMN location_lat")
        conn.exec_driver_sql("ALTER TABLE invoices DROP COLUMN location_lon")

    _ensure_location_columns(db)
    _backfill_location_data(db)


    db = Database(str(tmp_path / "location-columns.db"))
    db.create_tables()
    with db.get_session() as session:
        session.add(User(id="column-user", username="column-user", password_hash="x" * 60))
        session.commit()
    with db.get_session() as session:
        session.add(PaymentTransaction(id="column-tx", user_id="column-user", amount=1))
        session.add(Invoice(id="column-invoice", user_id="column-user"))
        session.add(Purchase(id="column-purchase", user_id="column-user"))
        session.commit()
    _ensure_location_columns(db)
    _ensure_location_columns(db)
    with db.engine.connect() as conn:
        columns = {
            table: {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for table in ("invoices", "payment_transactions", "purchases")
        }
    for table in columns.values():
        assert {"payment_lat", "merchant_lat", "purchase_context_type", "evidence_refs"} <= table


def test_invoice_mime_column_ensure_is_idempotent(tmp_path):
    db = Database(str(tmp_path / "mime-columns.db"))
    db.create_tables()
    _ensure_invoice_mime_column(db)
    _ensure_invoice_mime_column(db)
    with db.engine.connect() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(invoices)")}
    assert "mime_type" in columns
