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

# Install both protoc plugins:
#   protoc-gen-go      → generates nanorca.pb.go  (message types)
#   protoc-gen-go-grpc → generates nanorca_grpc.pb.go (service interfaces,
#                        UnimplementedExecutorServiceServer, etc.)
RUN GONOSUMDB='*' go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.34.1
RUN GONOSUMDB='*' go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.3.0

# Copy source
COPY . .

# Regenerate BOTH proto files from the authoritative .proto definition.
# This replaces both hand-written stubs with properly generated versions
# so UnimplementedExecutorServiceServer and ProtoReflect() are always correct.
RUN PATH="$PATH:$(go env GOPATH)/bin" protoc \
    --go_out=. \
    --go_opt=paths=source_relative \
    --go-grpc_out=. \
    --go-grpc_opt=paths=source_relative \
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
