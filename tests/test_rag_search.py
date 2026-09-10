"""Tests for Phase 4 RAG search (TF-IDF + cosine)."""
from __future__ import annotations

from src.domain.models import Invoice, InvoiceStatus
from src.search.embeddings import SimpleEmbedding, cosine_similarity, tokenize
from src.search.vector_store import InvoiceVectorStore


def _make_invoices():
    return [
        Invoice(id="a", invoice_number="1", vendor="Vinamilk", buyer="ABC",
                issue_date="2026-06-01", currency="VND", total=100, tax=10,
                status=InvoiceStatus.PAID, user_id="u1",
                raw_snippet="Hóa đơn Vinamilk sữa tươi"),
        Invoice(id="b", invoice_number="2", vendor="Acme Corp", buyer="Globex",
                issue_date="2026-07-01", currency="USD", total=5000, tax=500,
                status=InvoiceStatus.PAID, user_id="u1",
                raw_snippet="Acme consulting services"),
        Invoice(id="c", invoice_number="3", vendor="Globex", buyer="Acme",
                issue_date="2026-07-20", currency="USD", total=3200, tax=320,
                status=InvoiceStatus.UNPAID, user_id="u1",
                raw_snippet="Globex software license"),
    ]


def test_tokenize_basic():
    toks = tokenize("Vinamilk sữa tươi 100 hộp")
    assert "vinamilk" in toks
    assert "sữa" in toks
    assert "tươi" in toks
    # stopword dropped
    assert "của" not in tokenize("của")


def test_embedding_fit_and_vectorize():
    emb = SimpleEmbedding()
    emb.fit(["Vinamilk sữa", "Acme consulting", "Globex software"])
    v1 = emb.vectorize("Vinamilk")
    v2 = emb.vectorize("Vinamilk sữa")
    v3 = emb.vectorize("Acme")
    assert len(v1) == len(v2) == len(v3)
    # same query more similar than different vendor
    assert cosine_similarity(v1, v2) > cosine_similarity(v1, v3)


def test_index_and_search_returns_expected_vendor():
    store = InvoiceVectorStore(":memory:")
    store.index_many(_make_invoices())
    results = store.search("Vinamilk", user_id="u1", top_k=2)
    assert results, "expected at least one result"
    # top result should be the Vinamilk invoice
    assert results[0][0] == "a"


def test_vietnamese_query_matches():
    store = InvoiceVectorStore(":memory:")
    store.index_many(_make_invoices())
    results = store.search("sữa tươi", user_id="u1", top_k=3)
    assert results
    assert results[0][0] == "a"


def test_empty_query_returns_empty():
    store = InvoiceVectorStore(":memory:")
    store.index_many(_make_invoices())
    assert store.search("", user_id="u1") == []


def test_search_respects_user_scope():
    store = InvoiceVectorStore(":memory:")
    invs = _make_invoices()
    invs[0].user_id = "u1"
    invs[1].user_id = "u2"
    invs[2].user_id = "u1"
    store.index_many(invs)
    results = store.search("Vinamilk", user_id="u1", top_k=5)
    ids = [r[0] for r in results]
    assert "a" in ids
    assert "b" not in ids
