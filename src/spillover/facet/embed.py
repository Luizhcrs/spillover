from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding

_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5-Q"
EMBED_DIM = 768


@lru_cache(maxsize=1)
def _embedder() -> TextEmbedding:
    return TextEmbedding(model_name=_MODEL_NAME)


def embed_text(text: str) -> list[float]:
    """Embed a single string. Returns a 768-dim float list."""
    if not text:
        return [0.0] * EMBED_DIM
    vectors = list(_embedder().embed([text]))
    return list(vectors[0].tolist())
