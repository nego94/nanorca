# NANORCA Makefile
# Usage:
#   make proto     — Generate gRPC stubs from proto definition
#   make up        — Start all services in paper mode
#   make down      — Stop all services
#   make logs      — Tail logs from bot + executor
#   make build     — Build both Docker images
#   make test-go   — Run Go tests
#   make test-py   — Run Python tests
#   make lint-go   — Run Go linter
#   make lint-py   — Run Python linter (ruff)

.PHONY: proto up up-local down logs build test-go test-py lint-go lint-py status

# ── Proto codegen ─────────────────────────────────────────────────────────────
proto:
	@echo "Generating Go proto stubs..."
	protoc \
		--go_out=executor \
		--go_opt=paths=source_relative \
		--go-grpc_out=executor \
		--go-grpc_opt=paths=source_relative \
		-I executor/proto \
		executor/proto/nanorca.proto

	@echo "Generating Python proto stubs..."
	python3 -m grpc_tools.protoc \
		-I bot/proto \
		--python_out=bot/proto \
		--grpc_python_out=bot/proto \
		bot/proto/nanorca.proto

	@echo "✅ Proto stubs generated"

# ── Docker targets ────────────────────────────────────────────────────────────
build:
	docker compose build

up:
	PAPER_TRADING=true docker compose up -d

# ── Local laptop dev mode ─────────────────────────────────────────────────────
up-local:
	docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
	@echo ""
	@echo "✅ NANORCA running locally (paper mode)"
	@echo "📊 Grafana:    http://localhost:3000"
	@echo "📡 Metrics:    http://localhost:8080/metrics"
	@echo "🐘 Postgres:   localhost:5432 (for DB tools)"
	@echo "📊 Prometheus: http://localhost:9090"

down:
	docker compose down

restart:
	docker compose restart bot executor

logs:
	docker compose logs -f bot executor

logs-all:
	docker compose logs -f

# ── Go ────────────────────────────────────────────────────────────────────────
test-go:
	cd executor && go test ./...

lint-go:
	cd executor && go vet ./...

# ── Python ────────────────────────────────────────────────────────────────────
test-py:
	cd bot && python -m pytest tests/ -v

lint-py:
	cd bot && python -m ruff check .

# ── DB migrations (run manually if needed) ────────────────────────────────────
migrate:
	docker compose exec postgres psql -U $${POSTGRES_USER} -d $${POSTGRES_DB} \
		-f /docker-entrypoint-initdb.d/001_initial_schema.sql

# ── Status check ──────────────────────────────────────────────────────────────
status:
	docker compose ps
	@echo ""
	@echo "Metrics: http://localhost:8080/metrics"
	@echo "Grafana: http://localhost:3000"
