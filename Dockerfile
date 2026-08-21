FROM python:3.11-slim

# Prevents Python from buffering stdout/stderr, so `docker logs` shows
# output in real time instead of in delayed chunks -- important for a
# service where log timing matters when diagnosing a trading issue.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy just the requirements first so Docker's layer cache is reused
# (skipping the slow pip install) on every rebuild that only changes
# application code, not dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/

# Session tokens, logs, and any symbol-map file live here; docker-compose
# mounts this as a volume so they survive container recreation.
RUN mkdir -p /app/data

EXPOSE 5000

# A basic liveness check: /health requires no auth and never touches
# Webull, so it reflects "is the HTTP server up" rather than "is Webull
# reachable" -- those are different failure modes and conflating them
# would make Docker restart a perfectly healthy container just because
# Webull is temporarily down.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=3)" || exit 1

CMD ["python", "src/main.py"]
