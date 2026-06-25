"""
Ghost Requirement Agent — Slack Socket Mode Listener
=====================================================
Real-time Slack message ingestion using Slack Bolt + Socket Mode.

How it works:
  1. Maintains a persistent WebSocket to Slack (no public URL required).
  2. On every new message posted in any channel the bot is a member of,
     the handler fires automatically.
  3. The message is persisted to `raw_messages` (PostgreSQL) and enqueued
     on the `ghost.ingestion` Celery queue.
  4. The existing pipeline (GhostRequirementTeam → tasks.py) takes over
     from there — no changes to downstream logic.

Required environment variables:
  SLACK_BOT_TOKEN   xoxb-...  (Bot User OAuth Token — needs channels:history, channels:read)
  SLACK_APP_TOKEN   xapp-...  (App-Level Token with connections:write scope for Socket Mode)

Run standalone:
  PYTHONPATH=. python -m agents.slack_listener

Or via Docker Compose / Makefile:
  make slack-listener
"""

import uuid
import json
import logging
import signal
import sys
from datetime import datetime

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from agents.config import SLACK_BOT_TOKEN, SLACK_APP_TOKEN
from agents.db import get_db_cursor
from agents.tasks import process_ingestion_task
from agents.metrics import MESSAGES_INGESTED

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Validate required tokens at import time so failures surface early
# ─────────────────────────────────────────────────────────────────────────────

if not SLACK_BOT_TOKEN:
    logger.critical(
        "[SlackListener] SLACK_BOT_TOKEN is not set. "
        "Add it to your .env and restart."
    )
    sys.exit(1)

if not SLACK_APP_TOKEN:
    logger.critical(
        "[SlackListener] SLACK_APP_TOKEN is not set. "
        "Add it to your .env and restart."
    )
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Slack Bolt App (token-mode — signature verification is handled by Socket Mode)
# ─────────────────────────────────────────────────────────────────────────────

slack_app = App(token=SLACK_BOT_TOKEN)


# ─────────────────────────────────────────────────────────────────────────────
# Message Event Handler
# Fires for every `message` event delivered over the Socket Mode WebSocket.
# ─────────────────────────────────────────────────────────────────────────────

@slack_app.event("message")
def handle_message(event: dict, say, logger: logging.Logger):  # noqa: F811
    """
    Capture real Slack messages and push them into the Ghost pipeline.

    Filters applied (same as the HTTP webhook endpoint):
      - Ignore bot messages (subtype = bot_message, or bot_id present)
      - Ignore message edits (subtype = message_changed)
      - Ignore message deletions (subtype = message_deleted)
      - Ignore thread reply broadcasts that are re-posted to channel
      - Skip empty message bodies
    """
    subtype = event.get("subtype")
    bot_id = event.get("bot_id")

    # ── Filter: skip bot messages, edits, and deletions ─────────────────────
    if subtype or bot_id:
        logger.debug(
            f"[SlackListener] Skipping event: subtype={subtype} bot_id={bot_id}"
        )
        return

    text = event.get("text", "").strip()
    user = event.get("user", "U_UNKNOWN")
    channel = event.get("channel", "C_UNKNOWN")
    ts = event.get("ts", str(datetime.utcnow().timestamp()))
    team_id = event.get("team", "")

    # ── Filter: skip empty messages ──────────────────────────────────────────
    if not text:
        logger.debug("[SlackListener] Skipping empty message.")
        return

    logger.info(
        f"[SlackListener] New message captured — "
        f"channel={channel} user={user} text='{text[:80]}{'...' if len(text) > 80 else ''}'"
    )

    # ── Persist to raw_messages ──────────────────────────────────────────────
    message_id = uuid.uuid4()
    raw_payload = {
        "text": text,
        "user": user,
        "channel": channel,
        "ts": ts,
        "team_id": team_id,
        "source": "slack_socket_mode",
    }

    try:
        # Convert Slack ts (e.g. "1718000000.123456") to a safe float for to_timestamp()
        ts_float = float(ts) if len(ts.split(".")[0]) <= 10 else float(ts) / 1000.0

        with get_db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw_messages
                    (id, source_channel, author_identity, timestamp, raw_payload, processing_status)
                VALUES (%s, %s, %s, to_timestamp(%s), %s, 'pending')
                """,
                (
                    str(message_id),
                    channel,
                    user,
                    ts_float,
                    json.dumps(raw_payload),
                ),
            )

        logger.info(
            f"[SlackListener] Persisted raw message: id={message_id}"
        )

    except Exception as db_err:
        logger.error(
            f"[SlackListener] Failed to persist message to DB: {db_err}",
            exc_info=True,
        )
        # Do not enqueue if we couldn't persist — the task would have no row to read
        return

    # ── Enqueue to ghost.ingestion Celery queue ──────────────────────────────
    try:
        process_ingestion_task.delay(str(message_id))
        MESSAGES_INGESTED.labels(source="slack_socket_mode").inc()

        logger.info(
            f"[SlackListener] Enqueued message_id={message_id} → ghost.ingestion queue"
        )

    except Exception as queue_err:
        logger.error(
            f"[SlackListener] Failed to enqueue message_id={message_id}: {queue_err}",
            exc_info=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Graceful Shutdown Handler
# ─────────────────────────────────────────────────────────────────────────────

_handler: SocketModeHandler | None = None


def _shutdown(signum, frame):
    """Cleanly close the Socket Mode WebSocket on SIGTERM / SIGINT."""
    logger.info("[SlackListener] Shutdown signal received — closing WebSocket connection...")
    if _handler:
        _handler.close()
    logger.info("[SlackListener] Listener stopped.")
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global _handler

    logger.info("=" * 60)
    logger.info("  👻 Ghost Slack Listener — Socket Mode")
    logger.info("=" * 60)
    logger.info(
        f"  Bot token  : {SLACK_BOT_TOKEN[:12]}...{SLACK_BOT_TOKEN[-4:]}"
    )
    logger.info(
        f"  App token  : {SLACK_APP_TOKEN[:12]}...{SLACK_APP_TOKEN[-4:]}"
    )
    logger.info("  Monitoring : ALL channels the bot is invited to")
    logger.info("  Pipeline   : ghost.ingestion → Celery → AI Agents → Dashboard")
    logger.info("=" * 60)
    logger.info("[SlackListener] Starting WebSocket connection to Slack...")

    _handler = SocketModeHandler(
        app=slack_app,
        app_token=SLACK_APP_TOKEN,
    )

    # start() blocks until shutdown — the WebSocket auto-reconnects on drops
    _handler.start()


if __name__ == "__main__":
    # Ensure logging is configured when running as __main__
    import agents.config  # noqa: F401 — triggers logging.basicConfig()
    main()
