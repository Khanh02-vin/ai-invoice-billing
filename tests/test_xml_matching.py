from types import SimpleNamespace

import pytest

from src.domain.payments import PaymentTransaction, TxDirection, TxStatus
from src.extract.xml_parser import _to_float, parse_xml_invoice
from src.models import InvoiceStatus
from src.payments.matching import pick_invoices


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("110.000", 110000.0),
        ("1.234.567,89", 1234567.89),
        ("1,234,567.89", 1234567.89),
        ("123,45", 123.45),
        ("110000", 110000.0),
    ],
)
def test_to_float_parses_common_invoice_amount_formats(value, expected):
    assert _to_float(value) == expected


def test_parse_xml_invoice_with_namespace_and_vietnamese_amount():
    xml = b'''<HSoThueDTu xmlns="urn:invoice"><NDHDon><TTChung>
      <NBan><Ten>Shop</Ten><MST>123</MST></NBan>
      <THDon><SHDon>001</SHDon><Ngay>15/01/2026</Ngay></THDon>
      <DSHHD><HHD><TgTCThue>100.000</TgTCThue><TgTThue>10.000</TgTThue>
      <TgTTTBSo>110.000</TgTTTBSo></HHD></DSHHD>
    </TTChung></NDHDon></HSoThueDTu>'''
    invoice = parse_xml_invoice(xml)
    assert invoice is not None
    assert invoice.invoice_number == "001"
    assert invoice.total == 110000
    assert invoice.issue_date == "2026-01-15"


@pytest.mark.parametrize("content", [b"", b"not xml", b"<root><bad></root>", b"<root />"])
def test_parse_xml_invoice_rejects_invalid_or_non_invoice(content):
    assert parse_xml_invoice(content) is None


def _invoice(invoice_id, total, status=InvoiceStatus.UNPAID):
    return SimpleNamespace(
        id=invoice_id, total=total, currency="VND", status=status
    )


def _transaction(amount):
    return PaymentTransaction(
        id="tx", user_id="u", amount=amount, currency="VND",
        direction=TxDirection.DEBIT, status=TxStatus.PENDING_RECEIPT,
    )


def test_pick_invoices_prefers_single_and_skips_paid_invoice():
    invoices = [
        _invoice("paid", 50000, InvoiceStatus.PAID),
        _invoice("unpaid", 50000),
    ]
    assert [i.id for i in pick_invoices(_transaction(50000), invoices)] == ["unpaid"]


def test_pick_invoices_finds_smallest_combination():
    invoices = [_invoice("a", 20000), _invoice("b", 30000), _invoice("c", 60000)]
    assert [i.id for i in pick_invoices(_transaction(60000), invoices)] == ["c"]
    assert set(i.id for i in pick_invoices(_transaction(50000), invoices)) == {"a", "b"}


def test_pick_invoices_does_not_use_cancelled_or_paid_in_combination():
    invoices = [
        _invoice("paid", 20000, InvoiceStatus.PAID),
        _invoice("cancelled", 30000, InvoiceStatus.CANCELLED),
        _invoice("unpaid", 50000),
    ]
    assert [i.id for i in pick_invoices(_transaction(50000), invoices)] == ["unpaid"]
