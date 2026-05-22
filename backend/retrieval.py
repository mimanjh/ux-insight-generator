"""
retrieval.py — the "retrieve" half of the RAG pipeline.

build_index.py produced corpus/nng_index.npz offline. This module loads
that index and, given a query string, returns the most semantically
similar NNG articles.

The flow for one query:
  1. Embed the query with Voyage (input_type="query").
  2. Cosine-similarity it against every article vector in the index.
  3. Return the top-k articles, highest score first.

Cosine similarity reduces to a dot product because Voyage embeddings are
L2-normalized — we re-normalize defensively anyway so the math is correct
even if that ever changes.

The index is loaded once and cached at module scope: the FastAPI worker
pays the disk read on the first request only.
"""

import json
from functools import lru_cache

import numpy as np
import voyageai
from dotenv import load_dotenv

# Reuse the exact model id and index path from the build step. If these
# ever diverge, query and document vectors live in different spaces and
# retrieval silently returns garbage — so import, don't re-declare.
from backend.build_index import EMBED_MODEL, INDEX_PATH


class IndexUnavailable(RuntimeError):
    """Raised when nng_index.npz is missing. Caller should degrade
    gracefully (skip citations) rather than fail the whole analysis."""


@lru_cache(maxsize=1)
def _load_index() -> tuple[np.ndarray, list[dict]]:
    """Load and cache (vectors, metadata) from the .npz on disk.

    Returns:
        vectors: (N, D) float32, L2-normalized, one row per article.
        meta:    list of N dicts (id/title/url/themes/summary), row-aligned
                 with `vectors`.
    """
    if not INDEX_PATH.exists():
        raise IndexUnavailable(
            f"No index at {INDEX_PATH}. Run `python -m backend.build_index` "
            f"first (requires VOYAGE_API_KEY)."
        )

    data = np.load(INDEX_PATH, allow_pickle=False)
    vectors = data["vectors"].astype(np.float32)
    meta = json.loads(str(data["meta"]))

    # Normalize each row to unit length so a dot product == cosine
    # similarity. norm is shaped (N, 1) so it broadcasts over columns.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # guard against a zero vector
    vectors = vectors / norms

    return vectors, meta


def index_is_available() -> bool:
    """Cheap check for callers that want to skip RAG when unbuilt."""
    return INDEX_PATH.exists()


def _embed_queries(queries: list[str]) -> np.ndarray:
    """Embed query strings with Voyage and return unit-normalized rows.

    All queries go in one batched API call. input_type="query" (vs.
    "document" at index time) is Voyage's asymmetric-retrieval hint.
    """
    load_dotenv(override=True)
    client = voyageai.Client()
    result = client.embed(queries, model=EMBED_MODEL, input_type="query")

    vecs = np.array(result.embeddings, dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def retrieve_batch(queries: list[str], k: int = 4) -> list[list[dict]]:
    """Retrieve the top-k articles for each query string.

    Batched on purpose: the grounding step has one query per finding, and
    embedding them together is a single Voyage round-trip instead of N.

    Args:
        queries: one search string per finding.
        k:       how many candidate articles to return per query.

    Returns:
        A list parallel to `queries`. Each element is a list of up to k
        article dicts (id/title/url/themes/summary + a float "score" in
        roughly [-1, 1], higher = more similar), best first.
    """
    if not queries:
        return []

    vectors, meta = _load_index()  # raises IndexUnavailable if not built
    query_vecs = _embed_queries(queries)

    # (num_queries, D) @ (D, N) -> (num_queries, N) similarity matrix.
    scores = query_vecs @ vectors.T

    k = min(k, len(meta))
    results: list[list[dict]] = []
    for row in scores:
        # argpartition is O(N) to find the top-k, then we sort just those k.
        top_idx = np.argpartition(row, -k)[-k:]
        top_idx = top_idx[np.argsort(row[top_idx])[::-1]]
        results.append(
            [{**meta[i], "score": float(row[i])} for i in top_idx]
        )
    return results
