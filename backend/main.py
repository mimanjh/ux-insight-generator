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

Cache backend: Redis is required. Both analysis endpoints use worker threads.

Frontend mount: if frontend/dist exists (post `npm run build`), it is
served at `/` so the whole stack runs as one process in production.
"""

import base64
import hashlib
import json
import logging
import os
import time
import secrets
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

import redis
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl

from backend.analyze_screenshot import analyze_screenshot
from backend.capture import CaptureFailed, capture_url
from backend.ground_findings import ground_findings
from backend.models import Analysis

# Load .env early so REDIS_URL (and anything else env-driven) is available
# at module import time. override=True so .env values win over an empty/
# stale shell var — same gotcha that bit us on ANTHROPIC_API_KEY earlier.
load_dotenv(override=True)

logger = logging.getLogger("uvicorn.error")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Bump this whenever anything that affects model output changes:
# prompt text, model id, tool schema, theme taxonomy, etc. Old cache
# entries become unreachable instantly — no flush needed.
# v2: findings now carry a RAG-grounded `citation` field.
CACHE_VERSION = 9
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
ACCESS_KEY = os.environ.get("ANALYSIS_ACCESS_KEY", "")
HOURLY_LIMIT = int(os.environ.get("ANALYSIS_HOURLY_LIMIT", "30"))

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

# MIME types the analyzer understands. We never persist uploads to disk,
# so we don't need a MIME->extension map here.
ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}

app = FastAPI(title="UX Insight Generator")

# CORS for the Vite dev server (default port 5173).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def protect_analysis(request: Request, call_next):
    if request.url.path.rstrip("/") not in ("/api/analyze", "/api/analyze-image") or request.method != "POST":
        return await call_next(request)
    if not ACCESS_KEY:
        return JSONResponse({"detail": "Analysis access has not been configured by the owner."}, status_code=503)
    supplied = request.headers.get("authorization", "")
    if not secrets.compare_digest(supplied.encode(), f"Bearer {ACCESS_KEY}".encode()):
        return JSONResponse({"detail": "Enter a valid access code to run a review."}, status_code=401)
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        length = -1
    if length < 0 or request.headers.get("transfer-encoding"):
        return JSONResponse({"detail": "A fixed-length request body is required."}, status_code=411)
    limit = MAX_UPLOAD_BYTES + 65536 if request.url.path.rstrip("/").endswith("analyze-image") else 16384
    if length > limit:
        return JSONResponse({"detail": "The request is too large. Choose an image smaller than 5 MB."}, status_code=413)
    key = f"{REDIS_KEY_PREFIX}requests:{int(time.time()) // 3600}"
    try:
        count = await run_in_threadpool(r.eval, "local n = redis.call('INCR', KEYS[1]); if n == 1 then redis.call('EXPIRE', KEYS[1], 3600) end; return n", 1, key)
    except redis.RedisError:
        return JSONResponse({"detail": "Analysis is temporarily unavailable. Please try again."}, status_code=503)
    if count > HOURLY_LIMIT:
        return JSONResponse({"detail": "The hourly review limit has been reached. Please try again next hour."}, status_code=429, headers={"Retry-After": str(3600 - int(time.time()) % 3600)})
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response

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
        socket_timeout=3,
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
    refresh: bool = False
    context: str = Field(default="", max_length=1000)
    device: Literal["desktop", "mobile"] = "desktop"


class AnalyzeResponse(BaseModel):
    findings: Analysis
    cached: bool
    cache_saved: bool = True
    cache_key: str
    screenshot: str
    analyzed_at: str
    context: str
    device: Literal["desktop", "mobile", "upload"]


def cache_key_for_url(url: str, context: str = "", device: str = "desktop") -> str:
    identity = hashlib.sha256(json.dumps([url, context.strip(), device]).encode()).hexdigest()
    return f"{REDIS_KEY_PREFIX}analysis:v{CACHE_VERSION}:url:{identity}"


def cache_key_for_image(sha256_hex: str, context: str = "") -> str:
    # Same input bytes -> same key, regardless of filename or upload source.
    identity = hashlib.sha256(json.dumps([sha256_hex, context.strip()]).encode()).hexdigest()
    return f"{REDIS_KEY_PREFIX}analysis:v{CACHE_VERSION}:image:{identity}"


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
            detail="Saved reviews are temporarily unavailable.",
        )


def cached_response(key: str):
    try:
        value = r.get(key)
        return AnalyzeResponse(**json.loads(value), cached=True, cache_key=key) if value else None
    except (redis.RedisError, ValueError, TypeError):
        raise HTTPException(status_code=503, detail="Saved reviews are temporarily unavailable. Please try again.")


def run_analysis(key: str, load_image, context: str, device: str, refresh: bool = False):
    cached = cached_response(key)
    if cached and not refresh:
        return cached
    # ponytail: five-minute lease; add renewal if bounded provider calls ever exceed it.
    lock = r.lock(f"{key}:lock", timeout=300)
    try:
        acquired = lock.acquire(blocking=False)
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Analysis is temporarily unavailable. Please try again.")
    if not acquired:
        raise HTTPException(status_code=409, detail="This review is already running. Please try again shortly.")
    slot = None
    try:
        cached = cached_response(key)
        if cached and not refresh:
            return cached
        try:
            for index in range(2):
                candidate = r.lock(f"{REDIS_KEY_PREFIX}analysis-slot:{index}", timeout=300)
                if candidate.acquire(blocking=False):
                    slot = candidate
                    break
        except redis.RedisError:
            raise HTTPException(status_code=503, detail="Analysis is temporarily unavailable. Please try again.")
        if slot is None:
            raise HTTPException(status_code=429, detail="Both review slots are busy. Please try again shortly.")
        analyzed_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        try:
            image_bytes, media_type = load_image()
        except CaptureFailed as e:
            raise HTTPException(status_code=422, detail={"error": "capture_failed", "reason": e.reason, "hint": "Try uploading a screenshot of this page instead."})
        try:
            findings = Analysis.model_validate(analyze_screenshot(image_bytes, media_type, context=context))
            findings = Analysis.model_validate(ground_findings(findings.model_dump(mode="json")))
        except Exception as e:
            logger.warning("Analysis failed: %s", type(e).__name__)
            raise HTTPException(status_code=502, detail="The review could not be completed. Please try again.")
        response = AnalyzeResponse(
            findings=findings, cached=False, cache_key=key, context=context, device=device,
            screenshot=f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}",
            analyzed_at=analyzed_at,
        )
        try:
            r.setex(key, CACHE_TTL_SECONDS, response.model_dump_json(exclude={"cached", "cache_key", "cache_saved"}))
        except redis.RedisError:
            logger.warning("Completed review could not be cached")
            response.cache_saved = False
        logger.info("Analysis completed in %d ms", int((time.perf_counter() - started) * 1000))
        return response
    finally:
        if slot is not None:
            try:
                slot.release()
            except redis.RedisError:
                logger.warning("Analysis slot lease will expire")
        try:
            lock.release()
        except redis.RedisError:
            logger.warning("Analysis lock could not be released; its lease will expire")


@api.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    url, context = str(req.url), req.context.strip()
    return run_analysis(
        cache_key_for_url(url, context, req.device),
        lambda: capture_url(url, viewport=(390, 844) if req.device == "mobile" else (1440, 900), mobile=req.device == "mobile"),
        context, req.device, req.refresh,
    )


@api.post("/analyze-image", response_model=AnalyzeResponse)
def analyze_image(file: UploadFile = File(...), context: str = Form(default="", max_length=1000)):
    if file.content_type not in ALLOWED_IMAGE_MIME:
        raise HTTPException(status_code=400, detail="Choose a PNG, JPG, WEBP or GIF image.")
    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Choose an image smaller than 5 MB.")
    if not contents:
        raise HTTPException(status_code=400, detail="The image is empty. Choose another image.")
    context = context.strip()
    key = cache_key_for_image(hashlib.sha256(contents).hexdigest(), context)
    return run_analysis(key, lambda: (contents, file.content_type), context, "upload")


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
