# ── Go Executor Service — Dockerfile ──────────────────────────────────────────
# Multi-stage build: compile in full Go image, run in minimal alpine image.

# Stage 1: Build
FROM golang:1.22-alpine AS builder

WORKDIR /build

# Build tools + protoc (generates proper ProtoReflect() implementations from .proto)
RUN apk add --no-cache gcc musl-dev git protobuf

# Copy go.mod + go.sum first for layer caching
COPY go.mod go.sum ./
RUN go mod download

# Build protoc-gen-go from the already-downloaded module cache.
# GONOSUMDB=* uses go.sum for verification instead of sum.golang.org (no network needed).
# Only need protoc-gen-go (messages) — the gRPC stubs file is kept as-is.
RUN GONOSUMDB='*' go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.34.1

# Copy source
COPY . .

# Regenerate nanorca.pb.go from the authoritative .proto file.
# -I . so the relative input path is "proto/nanorca.proto", which makes
# protoc write to ./proto/nanorca.pb.go (overwriting the hand-written stub
# that had ProtoReflect()=nil and caused grpc-go to panic).
RUN PATH="$PATH:$(go env GOPATH)/bin" protoc \
    --go_out=. \
    --go_opt=paths=source_relative \
    -I . \
    proto/nanorca.proto

# Build binaries
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
