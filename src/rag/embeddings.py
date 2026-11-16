"""Embeddings locaux (fastembed, ONNX) au format attendu par LangChain."""

from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding
from langchain_core.embeddings import Embeddings

from src.config import EMBEDDING_MODEL


class EmbeddingsLocaux(Embeddings):
    """Modèle multilingue exécuté sur CPU : aucune donnée ne quitte la machine."""

    def __init__(self, modele: str = EMBEDDING_MODEL):
        self._modele = TextEmbedding(model_name=modele)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._modele.embed(texts, batch_size=64)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@lru_cache(maxsize=1)
def embeddings() -> EmbeddingsLocaux:
    return EmbeddingsLocaux()
