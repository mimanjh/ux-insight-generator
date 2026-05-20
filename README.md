# UX Insight Generator

Takes a URL or screenshot of a web page and returns a structured UX critique from Claude. A learning project for AI engineering fundamentals: prompt engineering, structured outputs via tool use, evaluation, caching, and a small full-stack app around the model call.

## What it does

- **Input:** a URL (captured via Playwright) or an uploaded image (PNG/JPG/WEBP/GIF, up to 5 MB).
- **Output:** structured JSON findings — what the model sees, what's working, and a ranked list of UX issues with severity, theme, observation/judgment confidence, and concrete fixes.
- Results are cached in Redis so re-analyzing the same URL or image costs nothing.

## Stack

- **Backend:** Python 3.12, FastAPI, Playwright (Chromium), Anthropic SDK, redis-py.
- **Frontend:** React + TypeScript + Vite.
- **Cache:** Redis (local Docker or Redis Cloud). Required — the backend refuses to start without a reachable Redis.
- **Model:** `claude-sonnet-4-5` for vision + structured output via tool use.

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
# Optionally:
#   REDIS_URL=redis://localhost:6379        (default if unset)
#   REDIS_KEY_PREFIX=uxinsight:             (default if unset)

# 5. Run backend (terminal A)
uvicorn backend.main:app --reload --port 8000

# 6. Run frontend dev server (terminal B)
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
> GET uxinsight:analysis:v1:url:https://example.com/
> DEL uxinsight:analysis:v1:url:...
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
