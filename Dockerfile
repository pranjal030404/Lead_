# Arthvex LeadGen - single image, runs app + scheduler on one $4/month box.
FROM python:3.13-slim

# tini reaps zombies and forwards SIGTERM, so a `docker stop` runs the app's
# shutdown hook (which releases the scheduler lease) instead of being killed.
# rclone is only needed if you set BACKUP_REMOTE; curl backs the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini curl rclone \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# Requirements first so a code change doesn't reinstall the dependency layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py ./

# Runs unprivileged. data/ and backups/ are volumes, so they're chowned before
# dropping root - a container that can't write its own database is a bad first run.
RUN useradd --create-home --uid 10001 leadgen \
    && mkdir -p /app/data /app/backups \
    && chown -R leadgen:leadgen /app
USER leadgen

VOLUME ["/app/data", "/app/backups"]
EXPOSE 8000

# Hits the one endpoint that needs no session, and it touches the database, so a
# healthy answer means the whole stack is actually up - not just the port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "run.py"]
