"""Local text embeddings for semantic search.

Groq does not expose an embeddings endpoint, so we embed notes locally with
`fastembed` (ONNX runtime, CPU-only, no PyTorch). The default model is
BAAI/bge-small-en-v1.5 (384 dims): small download (~130 MB), fast on CPU, and
consistently near the top of the MTEB retrieval leaderboard for its size.

Everything here degrades gracefully: if fastembed is not installed or the model
fails to download, `embed()` returns None and the storage layer falls back to
keyword search.
"""

from __future__ import annotations

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384

_model = None            # cached model instance once loaded
_unavailable = False     # set once we know loading is impossible, to avoid retrying


def _get_model():
    """Lazily load the embedding model. Returns None if unavailable."""
    global _model, _unavailable
    if _model is not None:
        return _model
    if _unavailable:
        return None
    try:
        from fastembed import TextEmbedding

        _model = TextEmbedding(model_name=MODEL_NAME)
        return _model
    except Exception as exc:  # ImportError, network failure, disk space, ...
        print(f"[embeddings] semantic search disabled: {exc}")
        _unavailable = True
        return None


def is_available() -> bool:
    """True if semantic search can be used."""
    return _get_model() is not None


def embed(texts: list[str], is_query: bool = False) -> np.ndarray | None:
    """Embed a list of strings into an (n, EMBED_DIM) float32 array.

    `is_query=True` uses the model's query encoder — BGE models are trained
    asymmetrically (a search query is embedded differently from a stored
    passage), which noticeably improves retrieval.

    Returns None if the model is unavailable so callers can fall back.
    """
    model = _get_model()
    if model is None:
        return None
    gen = model.query_embed(texts) if is_query else model.embed(texts)
    vectors = np.array(list(gen), dtype=np.float32)
    # Normalise so a dot product is cosine similarity.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def embed_one(text: str, is_query: bool = False) -> np.ndarray | None:
    """Embed a single string into a (EMBED_DIM,) float32 array, or None."""
    result = embed([text], is_query=is_query)
    return None if result is None else result[0]


def cosine_scores(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity between a normalised query vector and normalised rows."""
    return matrix @ query_vec
