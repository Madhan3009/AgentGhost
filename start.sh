#!/bin/bash
set -e

echo "🗄️ Running database schema initialisation..."
PYTHONPATH=. python -c "from agents.db import init_db; init_db()"

echo "⚙️ Starting Celery worker (concurrency=1 to conserve memory)..."
PYTHONPATH=. celery -A agents.celery_app worker \
  --queues=ghost.ingestion,ghost.embedding,ghost.reconciliation,ghost.approval,ghost.pr_analysis,ghost.dead_letter \
  --concurrency=1 \
  --loglevel=info &

echo "📡 Starting Slack Socket Mode listener..."
PYTHONPATH=. python -m agents.slack_listener &

echo "🚀 Starting FastAPI server on port $PORT..."
PYTHONPATH=. uvicorn agents.main:app --host 0.0.0.0 --port $PORT
