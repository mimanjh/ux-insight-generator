"""
FastAPI backend for the UX insight generator.

Endpoints (all under /api):
    GET  /api/health         — liveness + cache backend in use
    POST /api/analyze        — body {url}, returns {findings, cached, cache_key}
    POST /api/analyze-image  — multipart file upload; cache key is the
                               SHA-256 of the bytes, so re-uploading the
                               same image hits cache regardless of filename.

Run from project root:
    uvicorn backend.main:app --reload --port 8000

Cache backend: real Redis on localhost:6379 if reachable, else fakeredis
(in-process, dev-only). Start real Redis via Docker, Memurai, or WSL2.

Frontend mount: if frontend/dist exists (post `npm run build`), it is
served at `/` so the whole stack runs as one process in production.
"""

import hashlib
import json
import logging
import os
from pathlib import Path

import redis
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

from backend.analyze_screenshot import analyze_screenshot
from backend.capture import CaptureFailed, capture_url

# Load .env early so REDIS_URL (and anything else env-driven) is available
# at module import time. override=True so .env values win over an empty/
# stale shell var — same gotcha that bit us on ANTHROPIC_API_KEY earlier.
load_dotenv(override=True)

logger = logging.getLogger("uvicorn.error")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Bump this whenever anything that affects model output changes:
# prompt text, model id, tool schema, theme taxonomy, etc. Old cache
# entries become unreachable instantly — no flush needed.
CACHE_VERSION = 1
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h

# REDIS_URL drives the cache backend choice. Examples:
#   redis://localhost:6379                            (local Docker / Memurai)
#   redis://default:PASSWORD@HOST:PORT                (Redis Cloud free tier)
#   rediss://default:PASSWORD@HOST:PORT               (TLS — note extra 's')
# Default targets a local Redis on the standard port. Override in .env.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

# REDIS_KEY_PREFIX namespaces this project's keys so a single Redis instance
# can be shared across multiple projects without collision. Equivalent to
# ioredis's `keyPrefix` option, but applied explicitly at the key-construction
# boundary (redis-py has no built-in equivalent). Include the trailing colon
# so concatenation produces conventional Redis hierarchy notation.
# Examples: "uxinsight:", "myproject:", "team-a:".
REDIS_KEY_PREFIX = os.environ.get("REDIS_KEY_PREFIX", "uxinsight:")

UPLOAD_DIR = PROJECT_ROOT / "screenshots" / "uploads"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

# MIME type -> file extension. Mirrors what analyze_screenshot accepts.
MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

app = FastAPI(title="UX Insight Generator")

# CORS for the Vite dev server (default port 5173).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def _safe_redis_url(url: str) -> str:
    """Hide the password in REDIS_URL for log output.

    redis://user:secret@host:port  ->  redis://user:***@host:port
    """
    if "@" not in url:
        return url
    scheme_and_creds, host_part = url.rsplit("@", 1)
    if ":" in scheme_and_creds.split("//", 1)[-1]:
        head, _ = scheme_and_creds.rsplit(":", 1)
        return f"{head}:***@{host_part}"
    return url


def _build_redis_client():
    """Connect to Redis at REDIS_URL; raise on failure.

    Redis is a hard dependency: the app uses it for result caching and
    refuses to start without it. To run, point REDIS_URL at a reachable
    Redis (Docker, Memurai, WSL, or Redis Cloud).
    """
    safe_url = _safe_redis_url(REDIS_URL)
    client = redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=3,
    )
    try:
        client.ping()
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
        raise RuntimeError(
            f"Redis unreachable at {safe_url} ({type(e).__name__}: {e}). "
            f"Start Redis (e.g. `docker run -d -p 6379:6379 redis`) or set "
            f"REDIS_URL to a reachable instance."
        ) from e

    logger.info(
        "Connected to Redis at %s (key prefix: %r)",
        safe_url,
        REDIS_KEY_PREFIX,
    )
    return client


r = _build_redis_client()


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class AnalyzeResponse(BaseModel):
    findings: dict
    cached: bool
    cache_key: str


def cache_key_for_url(url: str) -> str:
    return f"{REDIS_KEY_PREFIX}analysis:v{CACHE_VERSION}:url:{url}"


def cache_key_for_image(sha256_hex: str) -> str:
    # Same input bytes -> same key, regardless of filename or upload source.
    return f"{REDIS_KEY_PREFIX}analysis:v{CACHE_VERSION}:image:{sha256_hex}"


api = APIRouter(prefix="/api")


@api.get("/health")
def health():
    """Cheap liveness check. Pings Redis; reports 503 if it has gone away."""
    try:
        r.ping()
        return {"status": "ok"}
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
        raise HTTPException(
            status_code=503,
            detail=f"Redis unreachable: {e}",
        )


@api.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    url = str(req.url)
    key = cache_key_for_url(url)

    # Cache lookup. Failures here (Redis down) should be a 503 — we
    # don't want to silently bypass the cache and rack up API charges.
    try:
        cached = r.get(key)
    except redis.exceptions.ConnectionError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Redis unreachable: {e}",
        )

    if cached:
        return AnalyzeResponse(
            findings=json.loads(cached),
            cached=True,
            cache_key=key,
        )

    # Cache miss: capture + analyze. Both steps are slow. The HTTP
    # connection will be held open for ~30s — fine for a learning
    # project, would queue in production.
    try:
        screenshot_path = capture_url(url)
    except CaptureFailed as e:
        # Capture failed in a way we recognized before spending an
        # Anthropic call. Return a structured 422 so the frontend can
        # surface the upload affordance as an alternative.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "capture_failed",
                "reason": e.reason,
                "hint": "Try uploading a screenshot of this page instead.",
            },
        )

    try:
        findings = analyze_screenshot(str(screenshot_path))
    except Exception as e:
        # The screenshot succeeded but Claude failed (network, rate limit,
        # bad bytes). Don't cache — likely transient.
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    r.setex(key, CACHE_TTL_SECONDS, json.dumps(findings))

    return AnalyzeResponse(findings=findings, cached=False, cache_key=key)


@api.post("/analyze-image", response_model=AnalyzeResponse)
async def analyze_image(file: UploadFile = File(...)):
    # Validate MIME type before reading any bytes.
    if file.content_type not in MIME_TO_EXT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported content type {file.content_type!r}. "
                f"Allowed: {', '.join(sorted(MIME_TO_EXT))}"
            ),
        )

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Image is {len(contents)} bytes; max is {MAX_UPLOAD_BYTES}. "
                f"Compress or resize before uploading."
            ),
        )

    # Identity-by-bytes: same image -> same hash -> same cache key.
    sha256_hex = hashlib.sha256(contents).hexdigest()
    key = cache_key_for_image(sha256_hex)

    try:
        cached = r.get(key)
    except redis.exceptions.ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"Redis unreachable: {e}")

    if cached:
        return AnalyzeResponse(
            findings=json.loads(cached),
            cached=True,
            cache_key=key,
        )

    # Persist to disk so the analyzer (which takes a path) can read it,
    # and so we have a debugging artifact for surprising results.
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = MIME_TO_EXT[file.content_type]
    image_path = UPLOAD_DIR / f"{sha256_hex}{ext}"
    image_path.write_bytes(contents)

    try:
        findings = analyze_screenshot(str(image_path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    r.setex(key, CACHE_TTL_SECONDS, json.dumps(findings))

    return AnalyzeResponse(findings=findings, cached=False, cache_key=key)


app.include_router(api)

# Serve the built frontend if it exists. In dev you run Vite separately
# (npm run dev on :5173) and the proxy forwards /api/* here. In prod you
# build with `npm run build` and this mount serves the SPA at /.
#
# Mount must come AFTER include_router so /api/* routes win over the
# static catch-all.
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=FRONTEND_DIST, html=True),
        name="frontend",
    )
    logger.info(f"Serving built frontend from {FRONTEND_DIST}")
else:
    logger.info(
        f"No built frontend at {FRONTEND_DIST} — API only. "
        "Run `npm run build` in frontend/ for the integrated mode."
    )
