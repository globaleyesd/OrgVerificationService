"""The real collaborators, built once and shared: database, embedder, file store and AI client."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .embeddings import make_embedder
from .files import make_file_store
from .llm import make_llm
from .repo import PgRepository


@dataclass
class Services:
    repo: object
    embedder: object
    files: object
    llm: object


def build_services(cfg: Config) -> Services:
    return Services(repo=PgRepository(cfg), embedder=make_embedder(cfg), files=make_file_store(cfg), llm=make_llm(cfg))
