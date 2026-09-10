"""Offline TF-IDF embeddings for invoice search.

Pure stdlib — no external ML deps. Tokenizes invoice text, builds a vocab of
the top-K most frequent terms, and produces L2-normalized dense vectors.
Similarity is cosine. Deterministic and fast enough for small invoice sets.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, Iterable, List, Sequence

_TOKEN_RE = re.compile(r"[a-z0-9àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]+", re.I)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "as", "at", "by", "from", "into",
    "của", "và", "có", "là", "được", "cho", "với", "một", "các", "những",
    "này", "đó", "tại", "trong", "đã", "sẽ", "đang", "về", "theo", "từ",
}


def tokenize(text: str) -> List[str]:
    """Lowercase + split on non-alphanumeric, drop stopwords + 1-char tokens."""
    if not text:
        return []
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


class SimpleEmbedding:
    """TF-IDF bag-of-words embedder with a fixed vocab (top-K by frequency)."""

    def __init__(self, max_vocab: int = 512, min_df: int = 1) -> None:
        self.max_vocab = max_vocab
        self.min_df = min_df
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self._is_fit = False

    # ---------- fit ----------

    def fit(self, texts: Sequence[str]) -> "SimpleEmbedding":
        """Build vocab + idf from a corpus."""
        df: Counter = Counter()
        tokenized: List[List[str]] = []
        for t in texts:
            toks = tokenize(t)
            tokenized.append(toks)
            df.update(set(toks))
        # keep terms with min_df, then top-K by df
        eligible = [(term, c) for term, c in df.items() if c >= self.min_df]
        eligible.sort(key=lambda x: x[1], reverse=True)
        top = eligible[: self.max_vocab]
        self.vocab = {term: i for i, (term, _c) in enumerate(top)}
        n = max(len(tokenized), 1)
        self.idf = {
            term: math.log((n + 1) / (c + 1)) + 1
            for term, c in top
        }
        self._is_fit = True
        return self

    # ---------- vectorize ----------

    def vectorize(self, text: str) -> List[float]:
        """Return L2-normalized dense vector for `text`."""
        if not self._is_fit or not self.vocab:
            return []
        vec = [0.0] * len(self.vocab)
        toks = tokenize(text)
        if not toks:
            return vec
        tf = Counter(toks)
        max_tf = max(tf.values()) or 1.0
        for term, count in tf.items():
            idx = self.vocab.get(term)
            if idx is None:
                continue
            tf_w = 0.5 + 0.5 * (count / max_tf)
            vec[idx] = tf_w * self.idf.get(term, 1.0)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    # ---------- helpers ----------

    def fit_vectorize(self, text: str) -> List[float]:
        """Fit on a single text then vectorize it (degenerate but safe)."""
        self.fit([text])
        return self.vectorize(text)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine between two vectors (already L2-normalized or not)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
