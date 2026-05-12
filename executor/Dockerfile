# ── Go Executor Service — Dockerfile ──────────────────────────────────────────
# Multi-stage build: compile in full Go image, run in minimal alpine image.

# Stage 1: Build
FROM golang:1.22-alpine AS builder

WORKDIR /build

# Install build tools (gcc for cgo, git for go mod)
RUN apk add --no-cache gcc musl-dev git

# Copy go.mod first (layer cache trick — only re-downloads if go.mod changes)
COPY go.mod ./

# Download what's declared in go.mod (partial — indirect deps resolved later)
RUN go mod download 2>/dev/null || true

# Now copy ALL source code (go mod tidy needs to see imports)
COPY . .

# Generate complete go.sum from actual source imports, then build
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -mod=mod -ldflags="-s -w" -o /app/executor ./cmd/server/
RUN CGO_ENABLED=0 GOOS=linux go build -mod=mod -ldflags="-s -w" -o /app/healthcheck ./cmd/healthcheck/

# Stage 2: Minimal runtime image
FROM alpine:3.20

RUN apk add --no-cache ca-certificates tzdata

WORKDIR /app

COPY --from=builder /app/executor    ./executor
COPY --from=builder /app/healthcheck ./healthcheck

# Non-root user for security
RUN addgroup -S nanorca && adduser -S nanorca -G nanorca
USER nanorca

EXPOSE 50051

ENTRYPOINT ["/app/executor"]
