#!/usr/bin/env python3
"""Offline RAG eval for ai-invoice-billing.

Builds a TF-IDF vector index over invoices, runs a fixed set of queries, and
prints Precision@K + latency. No external API / model download needed.

Usage:
    python scripts/eval_rag.py
    python scripts/eval_rag.py --db invoices.db
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import List, Tuple

from src.domain.models import Invoice, InvoiceStatus
from src.search.vector_store import InvoiceVectorStore
from src.store.repository import InvoiceRepository


@dataclass
class Case:
    query: str
    expected_vendor: str  # substring match (case-insensitive)


SAMPLE_INVOICES: List[Invoice] = [
    Invoice(
        id="inv-1", invoice_number="VN-001", vendor="Vinamilk",
        buyer="Công ty ABC", issue_date="2026-06-01", currency="VND",
        total=1200000, tax=120000, status=InvoiceStatus.PAID, user_id="u1",
        raw_snippet="Hóa đơn Vinamilk sữa tươi 100hộp",
    ),
    Invoice(
        id="inv-2", invoice_number="VN-002", vendor="TH True Milk",
        buyer="Công ty ABC", issue_date="2026-06-15", currency="VND",
        total=850000, tax=85000, status=InvoiceStatus.UNPAID, user_id="u1",
        raw_snippet="Hóa đơn TH True Milk sữa hạt",
    ),
    Invoice(
        id="inv-3", invoice_number="INT-100", vendor="Acme Corp",
        buyer="Globex", issue_date="2026-07-10", currency="USD",
        total=5000, tax=500, status=InvoiceStatus.PAID, user_id="u1",
        raw_snippet="Acme Corp consulting services July",
    ),
    Invoice(
        id="inv-4", invoice_number="INT-101", vendor="Globex",
        buyer="Acme Corp", issue_date="2026-07-20", currency="USD",
        total=3200, tax=320, status=InvoiceStatus.UNPAID, user_id="u1",
        raw_snippet="Globex software license renewal",
    ),
    Invoice(
        id="inv-5", invoice_number="VN-003", vendor="Vinamilk",
        buyer="Công ty XYZ", issue_date="2026-08-01", currency="VND",
        total=2500000, tax=250000, status=InvoiceStatus.PAID, user_id="u1",
        raw_snippet="Hóa đơn Vinamilk sữa chua 200hộp",
    ),
]

QUERIES: List[Case] = [
    Case("Vinamilk", "Vinamilk"),
    Case("sữa tươi", "Vinamilk"),
    Case("consulting", "Acme"),
    Case("software license", "Globex"),
    Case("hóa đơn VN", "Vinamilk"),
]


def precision_at_k(results: List[Tuple[str, float, str]], expected: str, k: int = 3) -> float:
    top = results[:k]
    if not top:
        return 0.0
    expected_lower = expected.lower()
    hits = sum(1 for _id, _score, text in top if expected_lower in (text or "").lower())
    return hits / len(top)


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG eval for invoices")
    parser.add_argument("--db", default=":memory:", help="SQLite path or :memory:")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    if args.db == ":memory:":
        invoices = SAMPLE_INVOICES
        repo = InvoiceRepository(":memory:")
        for inv in invoices:
            repo.upsert(inv)
    else:
        repo = InvoiceRepository(args.db)
        invoices = repo.list(limit=1000)

    store = InvoiceVectorStore(":memory:")
    store.index_many(invoices)

    print(f"Indexed {len(invoices)} invoices. Running {len(QUERIES)} queries (top_k={args.top_k})")
    total_p = 0.0
    total_ms = 0.0
    for case in QUERIES:
        t0 = time.perf_counter()
        results = store.search(case.query, top_k=args.top_k)
        ms = (time.perf_counter() - t0) * 1000
        total_ms += ms
        p = precision_at_k(results, case.expected_vendor, k=args.top_k)
        total_p += p
        print(f"  q={case.query!r:20} expected={case.expected_vendor!r:10} P@{args.top_k}={p:.2f} ({ms:.2f}ms)")
    print(f"Mean P@{args.top_k}: {total_p / len(QUERIES):.2f}  Mean latency: {total_ms / len(QUERIES):.2f}ms")


if __name__ == "__main__":
    main()
