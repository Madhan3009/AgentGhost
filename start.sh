#!/bin/bash
set -e

echo "🗄️ Running database schema initialisation..."
PYTHONPATH=. python -c "from agents.db import init_db; init_db()"

echo "⚙️ Starting Celery worker (concurrency=1 to conserve memory)..."
PYTHONPATH=. celery -A agents.celery_app worker \
  --queues=ghost.ingestion,ghost.embedding,ghost.reconciliation,ghost.approval,ghost.pr_analysis,ghost.dead_letter \
  --concurrency=1 \
  --loglevel=info &
CELERY_PID=$!
echo "✅ Celery worker started (PID=$CELERY_PID)"

# ── Slack listener watchdog ──────────────────────────────────────────────────
# Runs the listener in a loop with a 15s backoff between restarts.
# Without this, a crash silently kills the listener — no more messages reach
# the server. The 15s delay also prevents rapid reconnects that exhaust
# Slack's ~10 concurrent Socket Mode session limit per app token.
slack_watchdog() {
  while true; do
    echo "📡 Starting Slack Socket Mode listener..."
    PYTHONPATH=. python -m agents.slack_listener &
    SLACK_PID=$!
    echo "✅ Slack listener started (PID=$SLACK_PID)"
    wait $SLACK_PID
    EXIT_CODE=$?
    echo "⚠️  Slack listener exited (PID=$SLACK_PID, code=$EXIT_CODE). Restarting in 15s..."
    sleep 15
  done
}

slack_watchdog &
echo "✅ Slack watchdog started"

echo "🚀 Starting FastAPI server on port $PORT..."
PYTHONPATH=. uvicorn agents.main:app --host 0.0.0.0 --port $PORT
