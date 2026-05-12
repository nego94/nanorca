# ── Python Bot Service — Dockerfile ──────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# System deps for asyncpg (libpq) and grpcio (build)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user
RUN useradd -m nanorca && chown -R nanorca /app
USER nanorca

# Health: port 8081
EXPOSE 8080 8081

CMD ["python", "-m", "main"]
