FROM python:3.11-slim

# Fail fast, no .pyc, unbuffered logs so Railway captures output immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as a non-root user.
RUN useradd --create-home --uid 10001 miner && chown -R miner:miner /app
USER miner

# Railway injects PORT at runtime; 8080 is the local default.
ENV PORT=8080
EXPOSE 8080

# Single worker on purpose. The metrics ring buffer, response cache, and circuit
# breaker are per-process state — multiple workers would report split metrics and
# duplicate caches. The workload is async and I/O-bound, so one worker saturates
# fine. Note `sh -c` is required for ${PORT} expansion.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
