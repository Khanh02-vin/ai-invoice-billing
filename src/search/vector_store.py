"""SQLite-backed vector store for invoice search.

Stores one row per invoice with a JSON dense vector. Search is brute-force
cosine over the caller's scope (user/org) — fine for small-to-mid invoice sets
and keeps the project dependency-free.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from ..domain.models import Invoice
from ..store.db import SQLiteRepo
from .embeddings import SimpleEmbedding, cosine_similarity, tokenize


def _invoice_text(inv: Invoice) -> str:
    """Compose searchable text from an invoice."""
    parts = [
        inv.vendor or "",
        inv.buyer or "",
        inv.invoice_number or "",
        inv.raw_snippet or "",
        " ".join(item.description or "" for item in inv.line_items),
    ]
    return " ".join(p for p in parts if p)


class InvoiceVectorStore(SQLiteRepo):
    """Persists invoice embeddings and runs cosine search."""

    def __init__(self, db_path: str = "invoices.db") -> None:
        self._embedder = SimpleEmbedding()
        super().__init__(db_path)

    # ---------- schema ----------

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoice_embeddings (
                invoice_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                org_id TEXT,
                vector TEXT NOT NULL,
                text TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_emb_user ON invoice_embeddings(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_emb_org ON invoice_embeddings(org_id)"
        )

    # ---------- indexing ----------

    def index_invoice(self, inv: Invoice, org_id: str = "") -> None:
        """Embed + persist one invoice."""
        text = _invoice_text(inv)
        # fit on its own text so the vocab is non-empty even for the first doc
        vec = self._embedder.fit_vectorize(text)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO invoice_embeddings
                   (invoice_id, user_id, org_id, vector, text, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(invoice_id) DO UPDATE SET
                     user_id=excluded.user_id,
                     org_id=excluded.org_id,
                     vector=excluded.vector,
                     text=excluded.text,
                     updated_at=excluded.updated_at""",
                (
                    inv.id,
                    inv.user_id,
                    org_id,
                    json.dumps(vec),
                    text,
                    datetime.utcnow().isoformat(),
                ),
            )

    def index_many(self, invoices: Iterable[Invoice], org_id: str = "") -> int:
        """Fit embedder on the whole corpus, then persist each invoice."""
        invoices = list(invoices)
        if not invoices:
            return 0
        self._embedder.fit([_invoice_text(i) for i in invoices])
        n = 0
        for inv in invoices:
            text = _invoice_text(inv)
            vec = self._embedder.vectorize(text)
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO invoice_embeddings
                       (invoice_id, user_id, org_id, vector, text, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(invoice_id) DO UPDATE SET
                         user_id=excluded.user_id,
                         org_id=excluded.org_id,
                         vector=excluded.vector,
                         text=excluded.text,
                         updated_at=excluded.updated_at""",
                    (
                        inv.id,
                        inv.user_id,
                        org_id,
                        json.dumps(vec),
                        text,
                        datetime.utcnow().isoformat(),
                    ),
                )
            n += 1
        return n

    # ---------- search ----------

    def search(
        self,
        query: str,
        user_id: str = "",
        org_id: str = "",
        top_k: int = 5,
    ) -> List[Tuple[str, float, str]]:
        """Return top (invoice_id, score, text) for the query within scope."""
        if not query or not self._embedder._is_fit:
            return []
        q_vec = self._embedder.vectorize(query)
        if not q_vec:
            return []
        rows = self._fetch_candidates(user_id=user_id, org_id=org_id)
        scored: List[Tuple[str, float, str]] = []
        for inv_id, vec_json, text in rows:
            try:
                vec = json.loads(vec_json)
            except Exception:
                continue
            score = cosine_similarity(q_vec, vec)
            scored.append((inv_id, score, text))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def hybrid_search(
        self,
        query: str,
        user_id: str = "",
        org_id: str = "",
        top_k: int = 5,
    ) -> List[Tuple[str, float, str]]:
        """Vector search first; fall back to LIKE if embeddings missing."""
        results = self.search(query, user_id=user_id, org_id=org_id, top_k=top_k)
        if results:
            return results
        # LIKE fallback
        like = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT invoice_id, text FROM invoice_embeddings
                   WHERE (user_id = ? OR ? = '')
                     AND (org_id = ? OR ? = '')
                     AND text LIKE ?
                   LIMIT ?""",
                (user_id, user_id, org_id, org_id, like, top_k),
            ).fetchall()
        return [(r["invoice_id"], 0.0, r["text"]) for r in rows]

    # ---------- helpers ----------

    def _fetch_candidates(
        self, user_id: str, org_id: str
    ) -> List[Tuple[str, str, str]]:
        with self._connect() as conn:
            if org_id:
                rows = conn.execute(
                    "SELECT invoice_id, vector, text FROM invoice_embeddings WHERE org_id = ?",
                    (org_id,),
                ).fetchall()
            elif user_id:
                rows = conn.execute(
                    "SELECT invoice_id, vector, text FROM invoice_embeddings WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT invoice_id, vector, text FROM invoice_embeddings"
                ).fetchall()
        return [(r["invoice_id"], r["vector"], r["text"]) for r in rows]
