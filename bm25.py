"""Okapi BM25 keyword relevance, no dependencies. Used by ask.py (paper chunks)
and worldbank.py (indicator catalog)."""

import re

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+(?:[.%][0-9]+)?%?")
_STOPWORDS = frozenset(
    "the a an of to in and or for on by with is are was were be what which how do does did "
    "that this from at as their its it than more most".split())


def _terms(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


class BM25:
    """Keyword relevance (Okapi BM25) over a list of texts. Built at load
    (~0.1s for ~750 chunks) — no extra index file."""

    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75):
        docs = [_terms(t) for t in texts]
        lengths = np.array([len(d) for d in docs], dtype=np.float32)
        self.n = len(docs)
        self.postings: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        by_term: dict[str, list[tuple[int, int]]] = {}
        for i, doc in enumerate(docs):
            counts: dict[str, int] = {}
            for t in doc:
                counts[t] = counts.get(t, 0) + 1
            for t, tf in counts.items():
                by_term.setdefault(t, []).append((i, tf))
        norm = k1 * (1 - b + b * lengths / lengths.mean())
        for t, hits in by_term.items():
            ids = np.array([i for i, _ in hits])
            tf = np.array([f for _, f in hits], dtype=np.float32)
            idf = np.log(1 + (self.n - len(hits) + 0.5) / (len(hits) + 0.5))
            self.postings[t] = (ids, idf * tf * (k1 + 1) / (tf + norm[ids]))

    def scores(self, query: str) -> np.ndarray:
        out = np.zeros(self.n, dtype=np.float32)
        for t in _terms(query):
            if t in self.postings:
                ids, weights = self.postings[t]
                out[ids] += weights
        return out
