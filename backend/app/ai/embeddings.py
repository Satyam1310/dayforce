"""fastembed wrapper. The model is loaded lazily on first use (it downloads ~130 MB once),
so importing this module and memory-mode runs stay light."""

from __future__ import annotations

import os
from typing import Any

EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5; must match vector(384) in policy_chunks
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


class Embedder:
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = [[float(x) for x in v] for v in self._load().embed(texts)]
        for v in vectors:
            if len(v) != EMBEDDING_DIM:
                raise ValueError(
                    f"{self.model_name} gives {len(v)} dims, policy_chunks needs {EMBEDDING_DIM}"
                )
        return vectors
