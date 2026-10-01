"""Turns text into vectors for search.

local    a small model run on this machine through fastembed (no per-call fee). Downloaded on first use.
hashing  a simple word-hashing stand-in with no download. For tests and quick offline demos only: it matches
         shared words, not meaning.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from .config import Config


class Embedder(Protocol):
    dims: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    def __init__(self, dims: int):
        self.dims = dims

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dims
        for tok in re.findall(r"\w+", text.lower()):
            h = hashlib.sha1(tok.encode()).digest()
            v[int.from_bytes(h[:4], "big") % self.dims] += 1.0 if h[4] % 2 else -1.0
            v[int.from_bytes(h[5:9], "big") % self.dims] += 0.5
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts):
        return [self._one(t) for t in texts]

    def embed_query(self, text):
        return self._one(text)


class FastEmbedder:
    def __init__(self, model: str, dims: int):
        self.model_name, self.dims, self._model = model, dims, None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding   # imported on first use: the model downloads then
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def _check(self, vecs):
        out = [list(map(float, v)) for v in vecs]
        if out and len(out[0]) != self.dims:
            raise ValueError(f"The embedding model returns {len(out[0])} numbers but embeddings.dimensions is {self.dims}")
        return out

    def embed(self, texts):
        return self._check(self._load().embed(texts))

    def embed_query(self, text):
        m = self._load()
        gen = m.query_embed(text) if hasattr(m, "query_embed") else m.embed([text])
        return self._check(gen)[0]


def make_embedder(cfg: Config) -> Embedder:
    e = cfg.embeddings
    return HashingEmbedder(e.dimensions) if e.provider == "hashing" else FastEmbedder(e.model, e.dimensions)
