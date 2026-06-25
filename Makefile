# ============================================================
# Ghost Requirement Agent — Developer Makefile
# ============================================================
# Usage:
#   make help          Show this help
#   make up            Start Docker services (PostgreSQL + Redis)
#   make init-db       Initialize database schema + pgvector
#   make run-api       Start FastAPI server (port 8000)
#   make run-workers   Start all Celery workers
#   make run-dashboard Start Next.js dashboard (port 3000)
#   make dev           Start everything (DB + API + Workers + Dashboard)
#   make down          Stop Docker services
#   make logs          Tail Docker logs
#   make test          Run pipeline smoke test

SHELL := /bin/bash
VENV := ./agents_env
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
CELERY := $(VENV)/bin/celery
UVICORN := $(VENV)/bin/uvicorn

# Celery queue configuration (matches PROJECT_CONTEXT.md)
INGESTION_QUEUES := ghost.ingestion
EMBEDDING_QUEUES := ghost.embedding
RECONCILE_QUEUES := ghost.reconciliation
APPROVAL_QUEUES := ghost.approval
ALL_QUEUES := ghost.ingestion,ghost.embedding,ghost.reconciliation,ghost.approval,ghost.pr_analysis,ghost.dead_letter

.PHONY: help up down init-db run-api run-workers run-dashboard dev logs test clean lint run-vault seed-vault slack-listener slack-listener-logs

# ── Default Target ─────────────────────────────────────────

help: ## Show this help message
	@echo ""
	@echo "  👻  Ghost Requirement Agent — Make Commands"
	@echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ── Infrastructure ─────────────────────────────────────────

up: ## Start Docker services (PostgreSQL:5433 + Redis:6380)
	@echo "🐳 Starting Ghost infrastructure services..."
	docker compose up -d
	@echo "⏳ Waiting for services to be healthy..."
	@sleep 3
	@docker compose ps
	@echo "✅ Services started. PostgreSQL: localhost:5433, Redis: localhost:6380"

down: ## Stop Docker services
	@echo "🛑 Stopping Ghost infrastructure services..."
	docker compose down

logs: ## Tail Docker service logs
	docker compose logs -f

# ── Database ───────────────────────────────────────────────

init-db: ## Initialize PostgreSQL schema + pgvector extension
	@echo "🗄️  Initializing Ghost database schema..."
	PYTHONPATH=. $(PYTHON) -c "from agents.db import init_db; init_db()"
	@echo "✅ Database schema initialized."

# ── API Server ─────────────────────────────────────────────

run-api: ## Start FastAPI server on port 8000
	@echo "🚀 Starting Ghost FastAPI server on http://localhost:8000..."
	@echo "   API Docs: http://localhost:8000/api/docs"
	PYTHONPATH=. $(UVICORN) agents.main:app --host 0.0.0.0 --port 8000 --reload --log-level info

# ── Celery Workers ─────────────────────────────────────────

run-ingestion-worker: ## Start ingestion queue worker (4 concurrent, Agent 1)
	@echo "⚙️  Starting ingestion workers (ghost.ingestion queue, concurrency=4)..."
	PYTHONPATH=. $(CELERY) -A agents.celery_app worker \
		--queues=$(INGESTION_QUEUES) \
		--concurrency=4 \
		--loglevel=info \
		--hostname=ingestion@%h

run-embedding-worker: ## Start embedding queue worker (8 concurrent, Agent 2)
	@echo "⚙️  Starting embedding workers (ghost.embedding queue, concurrency=8)..."
	PYTHONPATH=. $(CELERY) -A agents.celery_app worker \
		--queues=$(EMBEDDING_QUEUES) \
		--concurrency=8 \
		--loglevel=info \
		--hostname=embedding@%h

run-reconcile-worker: ## Start reconciliation queue worker (4 concurrent, Agent 3)
	@echo "⚙️  Starting reconciliation workers (ghost.reconciliation queue, concurrency=4)..."
	PYTHONPATH=. $(CELERY) -A agents.celery_app worker \
		--queues=$(RECONCILE_QUEUES) \
		--concurrency=4 \
		--loglevel=info \
		--hostname=reconciliation@%h

run-approval-worker: ## Start approval queue worker (2 concurrent)
	@echo "⚙️  Starting approval workers (ghost.approval queue, concurrency=2)..."
	PYTHONPATH=. $(CELERY) -A agents.celery_app worker \
		--queues=$(APPROVAL_QUEUES) \
		--concurrency=2 \
		--loglevel=info \
		--hostname=approval@%h

run-workers: ## Start a single combined Celery worker handling all queues
	@echo "⚙️  Starting combined Celery workers for all queues..."
	@echo "   Queues: $(ALL_QUEUES)"
	PYTHONPATH=. $(CELERY) -A agents.celery_app worker \
		--queues=$(ALL_QUEUES) \
		--concurrency=4 \
		--loglevel=info \
		--hostname=ghost-worker@%h

run-flower: ## Start Celery Flower monitoring dashboard (port 5555)
	@echo "🌸 Starting Celery Flower on http://localhost:5555..."
	PYTHONPATH=. $(CELERY) -A agents.celery_app flower \
		--port=5555 \
		--broker_api=redis://localhost:6380/0

# ── Vault Secret Management ────────────────────────────────

run-vault: ## Start HashiCorp Vault dev server (port 8200, root token: root)
	@echo "🔐 Starting HashiCorp Vault dev server on http://localhost:8200..."
	@echo "   Root token: root  (dev only)"
	docker compose up -d vault
	@sleep 3
	@echo "✅ Vault ready. Run: make seed-vault"

seed-vault: ## Seed Vault dev server with all Ghost secrets from .env
	@echo "🌱 Seeding Ghost secrets into Vault KV v2..."
	@echo "   Reading from: .env and environment"
	@VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=root \
		vault secrets enable -path=secret kv-v2 2>/dev/null || true
	@VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=root \
		vault kv put secret/ghost/development \
		  GEMINI_API_KEY="$${GEMINI_API_KEY}" \
		  JWT_SECRET="$${JWT_SECRET:-ghost-agent-super-secret-key-12345678}" \
		  JWT_TOKEN_EXPIRY_MINUTES="$${JWT_TOKEN_EXPIRY_MINUTES:-60}" \
		  PII_SALT="$${PII_SALT:-ghost-pii-default-salt-value-987654321}" \
		  SLACK_SIGNING_SECRET="$${SLACK_SIGNING_SECRET:-}" \
		  GITHUB_WEBHOOK_SECRET="$${GITHUB_WEBHOOK_SECRET:-}" \
		  GITHUB_TOKEN="$${GITHUB_TOKEN:-}" \
		  DATABASE_URL="$${DATABASE_URL:-postgresql://ghost:ghostpassword@localhost:5433/ghost_poc}" \
		  REDIS_URL="$${REDIS_URL:-redis://localhost:6380/0}"
	@echo "✅ Secrets seeded at: secret/ghost/development"
	@echo "   Verify: VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=root vault kv get secret/ghost/development"

# ── Frontend Dashboard ─────────────────────────────────────

run-dashboard: ## Start Next.js dashboard on port 3000
	@echo "🖥️  Starting Ghost dashboard on http://localhost:3000..."
	cd app && npm run dev

# ── Slack Socket Mode Listener ──────────────────────────────────

slack-listener: ## Start real-time Slack Socket Mode listener (all channels)
	@echo "📡 Starting Ghost Slack Socket Mode listener..."
	@echo "   Monitoring: ALL channels the bot is invited to"
	@echo "   Pipeline:   ghost.ingestion → Celery → AI Agents → Dashboard"
	PYTHONPATH=. $(PYTHON) -m agents.slack_listener

slack-listener-logs: ## Tail Slack listener logs from Docker Compose
	docker compose logs -f slack-listener

# ── Full Dev Environment ───────────────────────────────────

dev: ## Start all services (infrastructure + API + workers + dashboard)
	@echo "👻 Starting Ghost Requirement Agent — Full Dev Environment"
	@echo "  Step 1: Starting Docker services..."
	$(MAKE) up
	@sleep 4
	@echo "  Step 2: Initializing database..."
	$(MAKE) init-db
	@echo ""
	@echo "  Step 3: Ready! Start the remaining services in separate terminals:"
	@echo ""
	@echo "    Terminal 1 — FastAPI:"
	@echo "      make run-api"
	@echo ""
	@echo "    Terminal 2 — Celery Workers:"
	@echo "      make run-workers"
	@echo ""
	@echo "    Terminal 3 — Next.js Dashboard:"
	@echo "      make run-dashboard"
	@echo ""
	@echo "    Terminal 4 — Slack Listener (real-time ingestion):"
	@echo "      make slack-listener"
	@echo ""
	@echo "  📊 Dashboard: http://localhost:3000/dashboard"
	@echo "  📡 API Docs:  http://localhost:8000/api/docs"
	@echo "  🌸 Flower:    http://localhost:5555 (run: make run-flower)"
	@echo "  🔐 Vault:     http://localhost:8200  (run: make run-vault && make seed-vault)"
	@echo ""
	@echo "  📡 Slack:     Listening on all channels (Socket Mode WebSocket)"
	@echo "  💡 Vault (optional): Set VAULT_ENABLED=true to resolve secrets from Vault."

# ── Testing ────────────────────────────────────────────────

test: ## Run pipeline smoke test (seed backlog + send mock message)
	@echo "🧪 Running Ghost pipeline smoke test..."
	@echo ""
	@echo "  1. Health check..."
	curl -sf http://localhost:8000/api/health | python3 -m json.tool
	@echo ""
	@echo "  2. Seeding backlog index..."
	curl -sf -X POST http://localhost:8000/api/test/seed-backlog | python3 -m json.tool
	@echo ""
	@echo "  3. Sending mock Slack message (should be a requirement)..."
	curl -sf -X POST http://localhost:8000/api/test/mock-slack-message \
		-H "Content-Type: application/json" \
		-d '{"text": "The login button MUST use color #1A73E8 on all mobile views. This is a hard requirement from the design system team.", "channel": "#product-design", "user": "priya_pm"}' \
		| python3 -m json.tool
	@echo ""
	@echo "  4. Sending noise message (should be filtered)..."
	curl -sf -X POST http://localhost:8000/api/test/mock-slack-message \
		-H "Content-Type: application/json" \
		-d '{"text": "Good morning everyone! Excited for the sprint review today 🎉", "channel": "#general", "user": "john_eng"}' \
		| python3 -m json.tool
	@echo ""
	@echo "  ⏳ Wait 10-15 seconds for workers to process, then check:"
	@echo "     curl http://localhost:8000/api/dashboard/actions | python3 -m json.tool"
	@echo "     curl http://localhost:8000/api/dashboard/stats | python3 -m json.tool"

test-pr: ## Run PR Auditor smoke test (sends mock PR diff that violates session timeout)
	@echo "🧪 Running Ghost PR Auditor smoke test..."
	@echo ""
	@echo "  1. Sending mock GitHub PR event (with timeout violation)..."
	curl -sf -X POST http://localhost:8000/api/test/mock-github-pr \
		-H "Content-Type: application/json" \
		-d '{"pr_number": 42, "repo_name": "acme/web-app", "title": "Increase session timeout to 40 mins", "diff_text": "diff --git a/src/config/session.js b/src/config/session.js\nindex 83a2d78..d3210ef\n--- a/src/config/session.js\n+++ b/src/config/session.js\n@@ -10,3 +10,3 @@\n-  sessionTimeout: 15 * 60 * 1000,\n+  sessionTimeout: 40 * 60 * 1000,"}' \
		| python3 -m json.tool
	@echo ""
	@echo "  ⏳ Wait 5-10 seconds for workers to process, then query the DB."

# ── Maintenance ────────────────────────────────────────────

install: ## Install Python dependencies in the virtualenv
	@echo "📦 Installing Python dependencies..."
	$(PIP) install -r requirements.txt
	@echo "✅ Dependencies installed."

lint: ## Run code linting
	$(VENV)/bin/ruff check agents/ || true

clean: ## Remove Python cache files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅ Cache cleaned."
