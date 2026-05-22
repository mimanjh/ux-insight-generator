"""
build_index.py — offline corpus embedding step for RAG.

This is the "ingest" half of a Retrieval-Augmented Generation pipeline.
It runs ONCE (or whenever the corpus changes), not per request:

    corpus/nng_articles.json  ->  [embed each summary]  ->  corpus/nng_index.npz

The .npz holds two things:
  - vectors: an (N, D) float32 matrix — one embedding row per article.
  - meta:    the article metadata (id/title/url/themes/summary), as JSON.

Keeping them together means retrieval.py loads a single file and the row
index of `vectors` lines up with the row index of `meta`.

Why no vector database? At ~25 articles a brute-force cosine similarity in
numpy is instant and needs zero infrastructure. A real vector DB (Redis
vector search, pgvector, etc.) earns its keep at 10k+ documents — not here.

Embeddings come from Voyage AI (Anthropic's recommended embeddings
partner — the Anthropic API itself has no embeddings endpoint). Voyage
returns L2-normalized vectors, so cosine similarity is just a dot product.

Run from the project root:
    python -m backend.build_index

Requires VOYAGE_API_KEY in .env (get one at https://www.voyageai.com/).
"""

import json
from pathlib import Path

import numpy as np
import voyageai
from dotenv import load_dotenv

# --- Config ---
# voyage-4 is Voyage's current general-purpose text embedding model. The
# whole corpus is one small batch, so model choice is about quality, not
# throughput. Must be a standard embedding model — NOT voyage-context-*
# (different endpoint) or voyage-multimodal-*/voyage-code-* (image/code).
EMBED_MODEL = "voyage-4"

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
CORPUS_PATH = CORPUS_DIR / "nng_articles.json"
INDEX_PATH = CORPUS_DIR / "nng_index.npz"


def load_corpus() -> list[dict]:
    """Read the curated article list from nng_articles.json."""
    raw = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    articles = raw["articles"]
    if not articles:
        raise ValueError(f"{CORPUS_PATH} has no articles to index.")
    return articles


def embed_text_for_article(article: dict) -> str:
    """The exact string we embed for one article.

    We embed `title + summary` rather than the summary alone: the title
    carries strong topical signal ("Placeholders in Form Fields Are
    Harmful") and costs almost nothing to include. Whatever string we
    build here MUST match the shape of the query string built in
    retrieval/grounding, or similarity scores drift.
    """
    return f"{article['title']}. {article['summary']}"


def build_index() -> None:
    # override=True so a .env value wins over an empty/stale shell var —
    # same gotcha the rest of this project guards against for API keys.
    load_dotenv(override=True)

    articles = load_corpus()
    print(f"Loaded {len(articles)} articles from {CORPUS_PATH.name}")

    texts = [embed_text_for_article(a) for a in articles]

    # One batched call embeds the whole corpus. input_type="document"
    # tells Voyage these are corpus entries (queries use input_type="query");
    # the asymmetry slightly improves retrieval relevance.
    client = voyageai.Client()
    print(f"Embedding {len(texts)} documents with {EMBED_MODEL}...")
    result = client.embed(texts, model=EMBED_MODEL, input_type="document")

    # Shape: (N articles, D dimensions). float32 keeps the file small and
    # is plenty of precision for cosine similarity.
    vectors = np.array(result.embeddings, dtype=np.float32)

    # Store metadata as a JSON string inside the .npz. The row order of
    # `meta` must match the row order of `vectors` — both come from the
    # same `articles` list, so they stay aligned.
    meta = json.dumps(
        [
            {
                "id": a["id"],
                "title": a["title"],
                "url": a["url"],
                "themes": a.get("themes", []),
                "summary": a["summary"],
            }
            for a in articles
        ]
    )

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(
        INDEX_PATH,
        vectors=vectors,
        meta=np.array(meta),  # 0-d string array
        model=np.array(EMBED_MODEL),
    )
    print(
        f"Wrote {INDEX_PATH.name}: {vectors.shape[0]} vectors "
        f"x {vectors.shape[1]} dims ({EMBED_MODEL})"
    )


if __name__ == "__main__":
    build_index()
