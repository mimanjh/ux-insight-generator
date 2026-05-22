# UX Insight Generator

Takes a URL or screenshot of a web page and returns a structured UX critique from Claude, with each finding grounded in a cited Nielsen Norman Group article via RAG. A learning project for AI engineering fundamentals: prompt engineering, structured outputs via tool use, retrieval-augmented generation, evaluation, caching, and a small full-stack app around the model call.

## What it does

- **Input:** a URL (captured via Playwright) or an uploaded image (PNG/JPG/WEBP/GIF, up to 5 MB).
- **Output:** structured JSON findings — what the model sees, what's working, and a ranked list of UX issues with severity, theme, observation/judgment confidence, and concrete fixes.
- **Citations:** each finding is grounded against a corpus of Nielsen Norman Group articles via RAG — retrieved by semantic similarity, then attached only if Claude judges the article genuinely supports the finding.
- Results are cached in Redis so re-analyzing the same URL or image costs nothing.

## Stack

- **Backend:** Python 3.12, FastAPI, Playwright (Chromium), Anthropic SDK, redis-py.
- **Frontend:** React + TypeScript + Vite.
- **Cache:** Redis (local Docker or Redis Cloud). Required — the backend refuses to start without a reachable Redis.
- **Model:** `claude-sonnet-4-5` for vision + structured output via tool use, and again for citation grounding.
- **Embeddings:** Voyage AI (`voyage-4`) for the RAG corpus and query embeddings. Stored as a numpy `.npz` — no vector database needed at this corpus size.

## Quickstart

Assumes Python 3.12, Node 22+, and Docker Desktop (or another Redis source).

```bash
# 1. Python deps
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

# 2. Frontend deps
cd frontend
npm install
cd ..

# 3. Start Redis (skip if you already have one)
docker run -d --name redis-cache -p 6379:6379 redis

# 4. Configure secrets
# Create .env in the project root with at minimum:
#   ANTHROPIC_API_KEY=sk-ant-...
#   VOYAGE_API_KEY=pa-...                   (for RAG embeddings)
# Optionally:
#   REDIS_URL=redis://localhost:6379        (default if unset)
#   REDIS_KEY_PREFIX=uxinsight:             (default if unset)

# 5. Build the RAG index (one-time; rerun when the corpus changes)
python -m backend.build_index

# 6. Run backend (terminal A)
uvicorn backend.main:app --reload --port 8000

# 7. Run frontend dev server (terminal B)
cd frontend
npm run dev
# open http://localhost:5173
```

The Vite dev server proxies `/api/*` to the backend, so the same fetch path works in dev and prod.

### Production build (single-process deployment)

```bash
cd frontend && npm run build && cd ..
uvicorn backend.main:app --port 8000
# Backend now serves the built SPA at / and the API at /api/* from one process.
```

## Configuration

All via `.env` (or shell environment).

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | — | API key for Claude. |
| `VOYAGE_API_KEY` | yes | — | API key for Voyage AI embeddings ([voyageai.com](https://www.voyageai.com/)). Used by `build_index.py` and at query time. Without it, analysis still works but findings get no citations. |
| `REDIS_URL` | no | `redis://localhost:6379` | Use `rediss://` for TLS. Format: `redis://[user:pass@]host:port[/db]`. |
| `REDIS_KEY_PREFIX` | no | `uxinsight:` | Lets one Redis instance host multiple projects without collisions. |

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | — | `{status, cache_backend}` |
| POST | `/api/analyze` | `{url}` JSON | `{findings, cached, cache_key}` |
| POST | `/api/analyze-image` | `multipart/form-data` with `file` | `{findings, cached, cache_key}` |

Capture failures (HTTP 4xx, bot challenges) return HTTP 422 with `{error: "capture_failed", reason, hint}` so the frontend can offer the upload path as a fallback.

## Project structure

```
ux-insight-generator/
├── backend/
│   ├── main.py              # FastAPI app + Redis cache + static mount
│   ├── analyze_screenshot.py # Claude vision + tool-use prompt (the core)
│   ├── capture.py           # Playwright capture with bot-evasion + fail-fast
│   ├── build_index.py       # RAG: embed the corpus -> nng_index.npz (offline)
│   ├── retrieval.py         # RAG: embed a query + cosine-rank the corpus
│   ├── ground_findings.py   # RAG: 2nd Claude call attaches cited articles
│   ├── corpus/
│   │   ├── nng_articles.json # Curated NNG article summaries (the corpus)
│   │   └── nng_index.npz    # Generated embeddings (gitignored)
│   ├── analyze_url.py       # CLI: URL -> screenshot -> findings
│   └── eval_consistency.py  # CLI: N-run consistency eval
├── frontend/                # Vite + React + TS
│   ├── src/App.tsx          # Main UI
│   └── vite.config.ts       # Includes /api proxy to backend in dev
├── runs/                    # Generated CLI outputs (JSON findings)
├── test_screenshots/        # Hand-curated fixtures
├── requirements.txt
└── .env                     # Not committed
```

## CLI usage

The scripts under `backend/` work as standalone tools, run as modules from project root:

```bash
# Analyze a local screenshot directly
python -m backend.analyze_screenshot test_screenshots/amazon_product.png

# Capture a URL to a PNG (no analysis). --output is required.
python -m backend.capture https://example.com --output out.png

# Capture + analyze + save findings
python -m backend.analyze_url https://news.ycombinator.com

# Run the same screenshot N times and report consistency
python -m backend.eval_consistency test_screenshots/amazon_product.png --runs 3
```

## RAG citation grounding

Each finding is grounded in a real NNG article so the critique cites evidence instead of asserting best practices from memory. The pipeline has two halves:

**Offline — build the index (`build_index.py`):**
1. `corpus/nng_articles.json` holds ~22 curated NNG articles, each with an original short summary and theme tags. (The summaries are written for this project — NNG article text is copyrighted and is *not* stored here.)
2. Each `title + summary` is embedded with Voyage `voyage-4` and saved to `corpus/nng_index.npz` (an `(N, D)` vector matrix + aligned metadata). Run once; rerun whenever the corpus changes.

**Per request — retrieve + ground (`retrieval.py` + `ground_findings.py`):**
3. After `analyze_screenshot` returns findings, each finding becomes a query string (`theme + title + what_i_see + why_it_matters`).
4. The queries are embedded and cosine-ranked against the index; the top ~4 articles per finding are the candidates.
5. A second Claude call (`attach_citations` tool) sees each finding with *only* its candidate articles and picks at most one — or declines. It may not cite anything outside the candidate list, and the backend validates every returned id against what was offered. That constraint is what prevents hallucinated citations.
6. Each finding gains a `citation` field: `null`, or `{article_id, title, url, relevance_note}`.

The step is non-fatal: if `nng_index.npz` is missing or Voyage/Claude errors, every finding just gets `citation: null` and the analysis returns normally.

To grow the corpus, add entries to `nng_articles.json` and rerun `python -m backend.build_index`. There is no vector database — brute-force cosine over a few dozen vectors is instant. A real vector store earns its place at 10k+ documents.

## Cache notes

The cache key has the shape:

```
{REDIS_KEY_PREFIX}analysis:v{CACHE_VERSION}:{url|image}:{identity}
```

- `REDIS_KEY_PREFIX` (env var): project namespace.
- `CACHE_VERSION` (constant in `backend/main.py`): bump whenever the prompt, schema, model, or theme taxonomy changes. Old keys become unreachable instantly and expire naturally.
- `url|image`: discriminator so a URL and an image upload can never collide.
- Identity: for URLs, the URL string (pydantic-normalized). For images, the SHA-256 of the file bytes — same image hits the same key regardless of filename.

TTL is 24h. Failures are not cached. To inspect or wipe the cache:

```bash
docker exec -it redis-cache redis-cli
> KEYS uxinsight:*                  # all this project's keys
> GET uxinsight:analysis:v2:url:https://example.com/
> DEL uxinsight:analysis:v2:url:...
```

## Known limitations

- **Bot-protected sites still fail.** LinkedIn, banks, paywalled news. The stealth tweaks in `capture.py` (realistic UA, `navigator.webdriver` masking, full Chrome-for-Testing channel) beat mid-tier detection but not high-end Cloudflare Bot Management. Image upload is the fallback.
- **HTTP 200 silent failures.** Cookie banners and login walls return 200 from Playwright's perspective; the analyzer will analyze the banner. Title-pattern matching catches the most common Cloudflare/captcha challenges, not all cases.
- **Cache hits are byte-identical only.** Two screenshots of the same page with one pixel different will not hit the same cache entry. See the discussion of equivalence levels in the project notes.
- **No request queue.** A single backend instance can hold one slow analysis open per worker. Fine for one user, would need a queue (RQ, Celery) at any real scale.
- **No auth.** The API is open. Don't expose it on the public internet without putting an auth layer in front.

## Development tips

- Backend reload on code change: add `--reload` to the `uvicorn` command.
- Frontend has HMR out of the box.
- Redis is required. If `redis-cli ping` doesn't return `PONG`, uvicorn will fail to start with a clear error pointing at `REDIS_URL`.
- When changing the prompt or schema, bump `CACHE_VERSION` in `backend/main.py` so stale entries don't get served.
