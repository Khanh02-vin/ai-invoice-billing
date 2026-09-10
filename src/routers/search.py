"""RAG search router (Phase 4).

GET /search?q=...&top_k=5  -> vector search over the caller's invoices.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..auth.security import decode_token
from ..domain.models import User
from ..errors import AppError
from ..search.vector_store import InvoiceVectorStore
from ..store.repository import InvoiceRepository
from ..store.users import UserRepository

router = APIRouter(prefix="/search", tags=["search"])

bearer = HTTPBearer(auto_error=False)

search_store = InvoiceVectorStore()
invoice_repo = InvoiceRepository()
user_repo = UserRepository()


def current_user_local(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> User:
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    user = user_repo.get(user_id) if user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    return user


@router.get("")
async def search_invoices(
    q: str = Query(..., min_length=1, description="Search query"),
    top_k: int = Query(5, ge=1, le=50),
    user: User = Depends(current_user_local),
):
    """Vector search over the caller's invoices. Auto-indexes if needed."""
    # Lazy index: if store has no rows for this user, build from repo.
    if not search_store._embedder._is_fit:
        invoices = invoice_repo.list(user_id=user.id, limit=1000)
        if invoices:
            search_store.index_many(invoices)
    results = search_store.search(q, user_id=user.id, top_k=top_k)
    return {
        "query": q,
        "results": [
            {"invoice_id": inv_id, "score": round(score, 4), "snippet": text[:200]}
            for inv_id, score, text in results
        ],
    }
