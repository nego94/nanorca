# ── Go Executor Service — Dockerfile ──────────────────────────────────────────
# Multi-stage build: compile in full Go image, run in minimal alpine image.

# Stage 1: Build
FROM golang:1.22-alpine AS builder

WORKDIR /build

# Install build tools
RUN apk add --no-cache gcc musl-dev git

# Copy go.mod + go.sum (generated locally via 'go mod tidy') for layer caching
COPY go.mod go.sum ./
RUN go mod download

# Copy source and build
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app/executor ./cmd/server/
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app/healthcheck ./cmd/healthcheck/

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
