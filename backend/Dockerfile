# ── Stage 1: builder ──────────────────────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /app

# System libraries required by daily-python (WebRTC audio stack).
# libgl1-mesa-glx was removed in Debian Trixie — use libgl1 + libglx-mesa0.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libstdc++6 \
    libglib2.0-0 \
    libgl1 \
    libglx-mesa0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-generated requirements.txt (run `uv export --no-dev -o requirements.txt` locally).
# Using a pre-generated file avoids needing uv or any external registry inside Docker.
COPY requirements.txt ./

# Install all dependencies via pip.
# --no-cache-dir : keeps the image lean
# --timeout 300  : generous timeout for large wheels (torch ~200MB, daily-python ~80MB)
RUN pip install --no-cache-dir --timeout 300 --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# Copy application source (after deps so this layer doesn't invalidate the dep layer)
COPY . .

# ── Stage 2: lean runtime image ────────────────────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

# Same OS libs are needed at runtime (the installed .so files link to them).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libstdc++6 \
    libglib2.0-0 \
    libgl1 \
    libglx-mesa0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --disabled-password --no-create-home --gecos "" appuser \
    && chown -R appuser /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

CMD ["gunicorn", "app.main:app", \
    "--workers", "2", \
    "--worker-class", "uvicorn.workers.UvicornWorker", \
    "--bind", "0.0.0.0:8000", \
    "--timeout", "120", \
    "--graceful-timeout", "10", \
    "--access-logfile", "-", \
    "--error-logfile", "-"]
