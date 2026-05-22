# ---------- Stage 1: build the frontend ----------
# Node 22 slim is small and matches what we use in dev. The build output
# is a static dist folder we copy into the runtime image.
FROM node:22-slim AS frontend
WORKDIR /build

# Copy only package files first so this layer caches when source changes.
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---------- Stage 2: Python runtime with Playwright ----------
# Microsoft maintains official Playwright images that ship the Chromium
# binary. Saves us a ~200 MB browser download at build time and avoids
# missing-system-dep issues that bite when you install Playwright on a
# slim base image manually.
#
# Use the `noble` (Ubuntu 24.04) tag, NOT `jammy` (22.04). noble ships
# Python 3.12, matching the local dev environment. jammy is Python 3.10,
# which is too old for some deps (e.g. numpy 2.4.x requires >= 3.11).
# Keep the container's Python aligned with dev to avoid "installs on my
# machine, fails in CI" build breaks.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python deps as a separate layer so requirements.txt is the only
# thing that busts the cache when application code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/ ./backend/

# Built frontend, copied in from the Node build stage. FastAPI's static
# mount expects this exact path (frontend/dist relative to project root).
COPY --from=frontend /build/dist ./frontend/dist

EXPOSE 8000

# host=0.0.0.0 is required so the container accepts connections from
# outside its network namespace (Fly's proxy reaches us this way).
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
