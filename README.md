# UX Insight Generator

Takes a URL or screenshot of a web page and returns a structured UX critique from Claude, with each finding grounded in a cited Nielsen Norman Group article via RAG. A learning project for AI engineering fundamentals: prompt engineering, structured outputs via tool use, retrieval-augmented generation, evaluation, and a small full-stack app around the model call.

## What it does

- **Input:** a URL (captured via Playwright) or an uploaded image (PNG/JPG/WEBP/GIF, up to 5 MB).
- **Output:** structured JSON findings : what the model sees, what's working, and a ranked list of UX issues with severity, theme, observation/judgment confidence, and concrete fixes.
- **Citations:** each finding is grounded against a corpus of Nielsen Norman Group articles via RAG : retrieved by semantic similarity, then attached only if Claude judges the article genuinely supports the finding.
- Each review uses your Anthropic API key for a fresh analysis. Enter it again for every submission; the app does not save keys or reports.

## Stack

- **Backend:** Python 3.12, FastAPI, Playwright (Chromium), Anthropic SDK, redis-py.
- **Frontend:** React + TypeScript + Vite.
- **Request limits:** Redis (local Docker or Redis Cloud). Required : the backend refuses to start without a reachable Redis.
- **Model:** `claude-sonnet-4-5` for vision + structured output via tool use, and again for citation grounding.
- **Embeddings:** Voyage AI (`voyage-4`) for the RAG corpus and query embeddings. Stored as a numpy `.npz` : no vector database needed at this corpus size.

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
#   VOYAGE_API_KEY=pa-...                   (for RAG embeddings)
# Optionally:
#   ANTHROPIC_API_KEY=sk-ant-...            (standalone CLI only)
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
| `ANALYSIS_HOURLY_LIMIT` | no | `30` | Shared hourly request limit across all users, including failed reviews. |
| `ANTHROPIC_API_KEY` | CLI only | none | Standalone scripts only. Web requests always require the caller's key. |
| `VOYAGE_API_KEY` | yes | : | API key for Voyage AI embeddings ([voyageai.com](https://www.voyageai.com/)). Used by `build_index.py` and at query time. Without it, analysis still works but findings get no citations. |
| `REDIS_URL` | no | `redis://localhost:6379` | Use `rediss://` for TLS. Format: `redis://[user:pass@]host:port[/db]`. |
| `REDIS_KEY_PREFIX` | no | `uxinsight:` | Lets one Redis instance host multiple projects without collisions. |

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | none | `{status}` |
| POST | `/api/analyze` | `{url, context?, device?}` JSON | `{findings, screenshot, analyzed_at, context, device}` |
| POST | `/api/analyze-image` | `multipart/form-data` with `file`, optional `context` | Same response |

Both analysis endpoints require `Authorization: Bearer <your Anthropic API key>`.
URL capture supports `device: "desktop" | "mobile"`; context is limited to 1,000
characters. Each finding has `citation_status`: `matched`, `no_match`, or `unavailable`.
Requests must include Content-Length; streamed/chunked uploads are rejected.

### API key handling and privacy

- Enter a workspace-scoped key for every review. Keys spanning workspaces need
  an additional workspace ID and are not supported by this form. The password field clears on submission, including
  failures, and page navigation. No localStorage, sessionStorage or cookies hold it.
- The key passes over HTTPS through this backend to Anthropic for the critique and
  citation call. It is request-local, never put in Redis, files, report exports,
  model prompts or application logs. It is not sent to captured websites or Voyage.
- This is not a guarantee against a compromised server, browser extension, network
  inspector, password manager or infrastructure logging. Only submit a key to a
  server you trust. Operators must not enable request-header tracing or body logging.
  Memory is not securely erased by JavaScript or Python.
- Anthropic calls use the supplied key and the fixed Anthropic API destination.
  Environment proxies and redirects are disabled. Nonempty `ANTHROPIC_CUSTOM_HEADERS`
  disables web analysis because the SDK could otherwise override authentication.
- Anthropic API usage is billed to the key's account, separately from Claude
  subscriptions. Use a dedicated, revocable key and appropriate account limits.
- Screenshots and context go to Anthropic. Generated finding text goes to Voyage
  for citation retrieval, billed to the operator's Voyage account. Provider retention
  policies apply; this app cannot promise that providers retain nothing.
- New reports and screenshots are not retained by the web app. Uploaded images
  may use framework-managed temporary files during processing. They remain
  on the current page until navigation, or in a download if the user exports one.
  API responses use `Cache-Control: no-store`. CLI tools still save their documented
  output files and can read an operator key from `.env`.

Existing deployments can remove `ANALYSIS_ACCESS_KEY` and the server's
`ANTHROPIC_API_KEY` after deploying this change, unless the latter is needed by
standalone CLI scripts. Previously cached reports are no longer read and expire
under their original 24-hour TTL; this change does not delete historical data.

Capture uses a local proxy that validates public IPv4 addresses and connects to the
checked numeric address. The same rule covers redirects and subresources. Only
ports 80/443 are allowed; IPv6-only sites and private-network pages require screenshot
upload. WebSockets and service workers are disabled during capture. Per-capture proxy
connections are limited to 32, with a 60-second/32-MB limit per connection.

Capture failures (HTTP 4xx, bot challenges) return HTTP 422 with `{error: "capture_failed", reason, hint}` so the frontend can offer the upload path as a fallback.

## Project structure

```
ux-insight-generator/
├── backend/
│   ├── main.py              # FastAPI app + Redis limits + static mount
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

**Offline : build the index (`build_index.py`):**
1. `corpus/nng_articles.json` holds ~22 curated NNG articles, each with an original short summary and theme tags. (The summaries are written for this project : NNG article text is copyrighted and is *not* stored here.)
2. Each `title + summary` is embedded with Voyage `voyage-4` and saved to `corpus/nng_index.npz` (an `(N, D)` vector matrix + aligned metadata). Run once; rerun whenever the corpus changes.

**Per request : retrieve + ground (`retrieval.py` + `ground_findings.py`):**
3. After `analyze_screenshot` returns findings, each finding becomes a query string (`theme + title + what_i_see + why_it_matters`).
4. The queries are embedded and cosine-ranked against the index; the top ~4 articles per finding are the candidates.
5. A second Claude call (`attach_citations` tool) sees each finding with *only* its candidate articles and picks at most one : or declines. It may not cite anything outside the candidate list, and the backend validates every returned id against what was offered. That constraint is what prevents hallucinated citations.
6. Each finding gains a `citation` field: `null`, or `{article_id, title, url, relevance_note}`.

The step is non-fatal: if `nng_index.npz` is missing or Voyage/Claude errors, every finding just gets `citation: null` and the analysis returns normally.

To grow the corpus, add entries to `nng_articles.json` and rerun `python -m backend.build_index`. There is no vector database : brute-force cosine over a few dozen vectors is instant. A real vector store earns its place at 10k+ documents.

## Request limits and validation

Redis stores hourly counters and short-lived locks, never user API keys, key hashes,
reports or screenshot bytes. Identical inputs share a content-hash lock while a
review runs; completed reviews are always fresh requests.

Offline validation: `python -m unittest discover -s tests`. After building the
frontend, run `python -m tests.browser_smoke` for the headless UI/API check. These
checks replace Redis and paid AI services with local test doubles. Also run
`python -m tests.capture_smoke` for real Chromium captures with offline DNS/transport
fixtures, including blocked private redirects and subresources. The Validate PR
workflow runs all of these checks plus an isolated Redis service check. To repeat
that integration check locally, explicitly supply a loopback test instance:
`python -m tests.redis_smoke redis://127.0.0.1:6379/15`. It uses a random test-only
key prefix and removes only its own keys afterward.

## Known limitations

- **Bot-protected sites still fail.** LinkedIn, banks, paywalled news. The stealth tweaks in `capture.py` (realistic UA, `navigator.webdriver` masking, full Chrome-for-Testing channel) beat mid-tier detection but not high-end Cloudflare Bot Management. Image upload is the fallback.
- **HTTP 200 silent failures.** Cookie banners and login walls return 200 from Playwright's perspective; the analyzer will analyze the banner. Title-pattern matching catches the most common Cloudflare/captcha challenges, not all cases.
- **No request queue.** Both endpoints run in worker threads. Redis limits the app to two concurrent reviews and one review per identical input; busy requests return 429 or 409. Locks expire after five minutes.
- **Shared capacity.** All users share the hourly limit. Key format checks happen before capture, but provider authentication happens during analysis. Fake keys can consume capture capacity or exhaust the shared quota. Add per-client abuse controls if public traffic requires them. Use HTTPS outside local development.

## Development tips

- Backend reload on code change: add `--reload` to the `uvicorn` command.
- Frontend has HMR out of the box.
- Redis is required. If `redis-cli ping` doesn't return `PONG`, uvicorn will fail to start with a clear error pointing at `REDIS_URL`.
