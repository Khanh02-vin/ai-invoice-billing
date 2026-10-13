"""RAG search package for ai-invoice-billing (Phase 4).

Offline, deterministic vector search over invoices using TF-IDF + cosine.
No external ML dependencies.
"""

from .embeddings import SimpleEmbedding, cosine_similarity, tokenize
from .vector_store import InvoiceVectorStore

__all__ = ["InvoiceVectorStore", "SimpleEmbedding", "cosine_similarity", "tokenize"]
