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

# Regenerate Python gRPC stubs from the authoritative proto file.
# grpcio-tools generates bare `import nanorca_pb2` which breaks when the file
# lives inside a package — patch it to a relative package import immediately.
RUN python -m grpc_tools.protoc \
    -I proto \
    --python_out=proto \
    --grpc_python_out=proto \
    proto/nanorca.proto && \
    sed -i 's/^import nanorca_pb2 as nanorca__pb2/from proto import nanorca_pb2 as nanorca__pb2/' \
        proto/nanorca_pb2_grpc.py

# Non-root user
RUN useradd -m nanorca && chown -R nanorca /app
USER nanorca

# Health: port 8081
EXPOSE 8080 8081

CMD ["python", "-m", "main"]
