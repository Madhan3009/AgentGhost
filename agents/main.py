"""
Ghost Requirement Agent - FastAPI Application
Webhook receivers, dashboard API endpoints, and developer testing utilities.

Phase 1: Slack/Teams webhook receivers, PostgreSQL persistence
Phase 3: Dashboard API, human approval gates
Phase 4: JWT auth middleware, GitHub PR webhook, Prometheus metrics
"""
import uuid
import json
import hmac
import hashlib
import time
import traceback
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from agents.config import GEMINI_API_KEY, SLACK_SIGNING_SECRET, GITHUB_WEBHOOK_SECRET, IS_DEVELOPMENT, validate_config
from agents.db import get_db_cursor, init_db
from agents.llm import get_embedding, get_query_embedding
from agents.tasks import process_ingestion_task, approve_action_task, process_pr_analysis_task
from agents.auth import verify_credentials, create_access_token, require_reviewer
from agents.vault import get_vault_health
from agents.metrics import (
    MESSAGES_INGESTED, APPROVALS, PENDING_ACTIONS, PR_AUDITS
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Ghost Requirement Agent API",
    description=(
        "Autonomous pipeline for capturing undocumented product requirements from "
        "Slack/Teams and reconciling them against the engineering backlog."
    ),
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# CORS: Allow Next.js dashboard (port 3000) + development origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "*"] if IS_DEVELOPMENT else ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Startup Events
# ─────────────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Initialize database schema and mount Prometheus instrumentation on startup."""
    logger.info("Ghost Requirement Agent API starting up...")
    validate_config()
    try:
        init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        # Don't crash startup — DB might already be initialized
    # Log Vault status
    vault_status = get_vault_health()
    if vault_status.get("enabled"):
        logger.info(f"[Vault] Status: {vault_status.get('status')} addr={vault_status.get('addr')}")
    else:
        logger.info("[Vault] Disabled — using environment variables for secrets.")

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus Instrumentation
# Exposes /metrics for Prometheus scraping.
# Must be mounted AFTER app is created but BEFORE first request is served.
# ─────────────────────────────────────────────────────────────────────────────

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_respect_env_var=False,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/api/health", "/metrics"],
    inprogress_name="ghost_http_requests_inprogress",
    inprogress_labels=True,
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# ─────────────────────────────────────────────────────────────────────────────
# Request Models
# ─────────────────────────────────────────────────────────────────────────────

class SlackMessageInput(BaseModel):
    """Developer testing: inject a mock Slack message into the pipeline."""
    text: str
    channel: Optional[str] = "#product-design"
    user: Optional[str] = "U_MOCK_USER"

class SlackUrlVerification(BaseModel):
    """Slack API URL verification challenge response."""
    challenge: str

class GithubPRInput(BaseModel):
    """Developer testing: inject a mock GitHub PR webhook payload."""
    pr_number: int
    repo_name: str
    title: str = "Mock Pull Request"
    diff_text: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Authentication Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/auth/token", tags=["auth"])
def issue_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Issue a JWT Bearer token for authenticating dashboard mutation requests.

    Dev credentials: username=admin / password=ghostadmin

    Returns:
        access_token: HS256 signed JWT (valid for JWT_TOKEN_EXPIRY_MINUTES)
        token_type: 'bearer'
    """
    user = verify_credentials(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(subject=user["username"], role=user["role"])
    logger.info(f"[Auth] Token issued for user='{user['username']}' role='{user['role']}'")
    return {"access_token": token, "token_type": "bearer"}


# ─────────────────────────────────────────────────────────────────────────────
# Utility: Slack Request Signature Verification
# ─────────────────────────────────────────────────────────────────────────────

def verify_slack_signature(
    raw_body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: str
) -> bool:
    """
    Verify Slack webhook request authenticity using HMAC-SHA256.
    See: https://api.slack.com/authentication/verifying-requests-from-slack
    """
    if not signing_secret:
        logger.warning("SLACK_SIGNING_SECRET not configured — skipping verification in dev mode")
        return True
    
    # Reject requests older than 5 minutes (replay attack protection)
    try:
        if abs(time.time() - int(timestamp)) > 300:
            logger.warning("Slack request timestamp is too old — possible replay attack")
            return False
    except (ValueError, TypeError):
        return False
    
    base_string = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    computed = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(computed, signature)

# ─────────────────────────────────────────────────────────────────────────────
# Health Check Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health", tags=["system"])
def health():
    """Basic health check."""
    return {
        "status": "ok",
        "service": "Ghost Requirement Agent API",
        "gemini_configured": bool(GEMINI_API_KEY),
        "environment": "development" if IS_DEVELOPMENT else "production"
    }

@app.get("/api/health/detailed", tags=["system"])
def health_detailed():
    """
    Detailed health check: verifies DB connectivity and Redis availability.
    """
    checks = {}
    
    # Database check
    try:
        with get_db_cursor() as cur:
            cur.execute("SELECT 1")
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}
    
    # Message counts
    try:
        with get_db_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM raw_messages")
            msg_count = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) FROM extracted_requirements")
            req_count = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) FROM backlog_index")
            backlog_count = cur.fetchone()["count"]
        checks["data"] = {
            "raw_messages": msg_count,
            "extracted_requirements": req_count,
            "backlog_tickets": backlog_count
        }
    except Exception as e:
        checks["data"] = {"status": "error", "detail": str(e)}
    
    overall_status = "ok" if all(
        v.get("status") != "error" for v in checks.values() if isinstance(v, dict)
    ) else "degraded"
    
    return {
        "status": overall_status,
        "checks": checks,
        "gemini_configured": bool(GEMINI_API_KEY),
        "slack_secret_configured": bool(SLACK_SIGNING_SECRET),
        "vault": get_vault_health(),
    }

# ─────────────────────────────────────────────────────────────────────────────
# Slack Webhook Receiver (FR-01)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/webhooks/slack", tags=["webhooks"])
async def webhook_slack(request: Request):
    """
    FR-01: Slack Bolt API webhook receiver.
    Persists message to raw_messages within 2s, enqueues to ghost.ingestion queue.
    
    Handles:
    - Slack URL verification challenge
    - Event callback with message events
    """
    raw_body = await request.body()
    
    # Slack signature verification (production hardening)
    if SLACK_SIGNING_SECRET:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        if not verify_slack_signature(raw_body, timestamp, signature, SLACK_SIGNING_SECRET):
            raise HTTPException(status_code=401, detail="Invalid Slack signature")
    
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    # Handle Slack URL verification challenge
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    
    # Handle event callbacks
    event = payload.get("event", {})
    event_type = event.get("type", "")
    
    # Only process message events (not bot messages, edits, or deletes)
    if event_type != "message" or event.get("subtype"):
        return {"status": "ignored", "reason": f"Unsupported event type: {event_type}"}
    
    text = event.get("text", "").strip()
    user = event.get("user", "U_UNKNOWN")
    channel = event.get("channel", "C_UNKNOWN")
    ts = event.get("ts", str(datetime.utcnow().timestamp()))
    
    if not text:
        return {"status": "ignored", "reason": "empty_message_text"}
    
    # FR-01: Persist to raw_messages within 2s (synchronous DB write)
    message_id = uuid.uuid4()
    
    try:
        with get_db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw_messages (id, source_channel, author_identity, timestamp, raw_payload, processing_status)
                VALUES (%s, %s, %s, to_timestamp(%s), %s, 'pending')
                """,
                (
                    str(message_id),
                    channel,
                    user,
                    # Handle both second-precision and millisecond timestamps
                    float(ts) if len(ts.split(".")[0]) <= 10 else float(ts) / 1000.0,
                    json.dumps({
                        "text": text,
                        "user": user,
                        "channel": channel,
                        "ts": ts,
                        "team_id": payload.get("team_id", "")
                    }),
                )
            )
        
        # Enqueue to ghost.ingestion queue (Celery)
        process_ingestion_task.delay(str(message_id))
        
        # Prometheus: track ingestion by source
        MESSAGES_INGESTED.labels(source="slack").inc()

        logger.info(f"Message ingested: id={message_id} channel={channel}")
        return {"status": "queued", "message_id": str(message_id)}
        
    except Exception as e:
        logger.error(f"Webhook error: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to persist message: {str(e)}")

# ─────────────────────────────────────────────────────────────────────────────
# MS Teams Webhook (FR-01 Extension)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/webhooks/teams", tags=["webhooks"])
async def webhook_teams(request: Request):
    """
    FR-01: MS Teams connector webhook receiver.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    # MS Teams adaptive card / connector format
    text = (
        payload.get("text") or 
        payload.get("body", {}).get("content", "") or
        ""
    ).strip()
    
    if not text:
        return {"status": "ignored", "reason": "no_text_content"}
    
    # Extract Teams metadata
    from_user = payload.get("from", {})
    user_id = from_user.get("id", "teams_user")
    user_name = from_user.get("name", "Teams User")
    channel_data = payload.get("channelData", {})
    channel = channel_data.get("channel", {}).get("name", "teams-general")
    
    message_id = uuid.uuid4()
    ts = str(datetime.utcnow().timestamp())
    
    with get_db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_messages (id, source_channel, author_identity, timestamp, raw_payload, processing_status)
            VALUES (%s, %s, %s, NOW(), %s, 'pending')
            """,
            (
                str(message_id),
                f"teams-{channel}",
                user_name,
                json.dumps({"text": text, "user": user_name, "channel": channel, "ts": ts, "source": "teams"}),
            )
        )
    
    process_ingestion_task.delay(str(message_id))

    # Prometheus: track ingestion by source
    MESSAGES_INGESTED.labels(source="teams").inc()

    return {"status": "queued", "message_id": str(message_id), "source": "teams"}


def verify_github_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Validate incoming GitHub Webhook signature."""
    if not secret:
        logger.warning("[Webhook/GitHub] GITHUB_WEBHOOK_SECRET not configured - signature check bypassed")
        return True
    if not signature:
        logger.error("[Webhook/GitHub] Missing signature header")
        return False
    if signature.startswith("sha256="):
        signature = signature[7:]
    
    computed = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


@app.post("/api/webhooks/github", tags=["webhooks"])
async def github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None)
):
    """
    FR-04: GitHub Webhook Receiver.
    Listens for pull request events, verifies signature, and enqueues PR analysis.
    """
    raw_body = await request.body()
    
    # 1. Verify Signature
    if not verify_github_signature(raw_body, x_hub_signature_256, GITHUB_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")
        
    # 2. Check Event Type
    if x_github_event != "pull_request":
        logger.info(f"[Webhook/GitHub] Ignoring event: {x_github_event}")
        return {"status": "ignored", "event": x_github_event}
        
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
        
    action = payload.get("action")
    # We care about new or updated PRs
    if action not in ["opened", "reopened", "synchronize"]:
        logger.info(f"[Webhook/GitHub] Ignoring PR action: {action}")
        return {"status": "ignored", "action": action}
        
    pr_data = payload.get("pull_request", {})
    pr_number = payload.get("number")
    repo_name = payload.get("repository", {}).get("full_name")
    pr_title = pr_data.get("title", "")
    diff_url = pr_data.get("diff_url", "")
    
    if not pr_number or not repo_name:
        raise HTTPException(status_code=400, detail="Missing repository name or PR number")
        
    logger.info(f"[Webhook/GitHub] Received PR event for {repo_name} #{pr_number}: {pr_title}")
    
    task_payload = {
        "pr_number": pr_number,
        "repo_name": repo_name,
        "title": pr_title,
        "diff_url": diff_url,
        "action": action
    }
    
    # Enqueue PR analysis
    process_pr_analysis_task.delay(task_payload)
    
    return {"status": "queued", "repo": repo_name, "pr": pr_number}


# ─────────────────────────────────────────────────────────────────────────────
# Developer Testing Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/test/mock-slack-message", tags=["developer"])
def mock_slack_message(input_data: SlackMessageInput):
    """
    Developer Panel: Inject a mock Slack message into the pipeline.
    Simulates FR-01 webhook ingestion for local testing.
    """
    message_id = uuid.uuid4()
    ts = str(datetime.utcnow().timestamp())
    
    with get_db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_messages (id, source_channel, author_identity, timestamp, raw_payload, processing_status)
            VALUES (%s, %s, %s, NOW(), %s, 'pending')
            """,
            (
                str(message_id),
                input_data.channel,
                input_data.user,
                json.dumps({
                    "text": input_data.text,
                    "user": input_data.user,
                    "channel": input_data.channel,
                    "ts": ts,
                    "source": "mock"
                }),
            )
        )
    
    # Enqueue to ghost.ingestion Celery queue
    process_ingestion_task.delay(str(message_id))
    
    logger.info(f"Mock message queued: id={message_id}")
    return {"status": "queued", "message_id": str(message_id)}


@app.post("/api/test/mock-github-pr", tags=["developer"])
def mock_github_pr(input_data: GithubPRInput):
    """
    Developer Panel: Inject a mock GitHub PR webhook event.
    Enqueues the PR diff analysis task directly.
    """
    task_payload = {
        "pr_number": input_data.pr_number,
        "repo_name": input_data.repo_name,
        "title": input_data.title,
        "diff_url": f"https://github.com/{input_data.repo_name}/pull/{input_data.pr_number}.diff",
        "action": "opened",
        "diff_text": input_data.diff_text  # Passed directly for mocking
    }
    
    process_pr_analysis_task.delay(task_payload)
    
    logger.info(f"Mock GitHub PR queued: repo={input_data.repo_name} #{input_data.pr_number}")
    return {"status": "queued", "repo": input_data.repo_name, "pr": input_data.pr_number}


@app.post("/api/test/seed-backlog", tags=["developer"])
def seed_backlog():
    """
    Developer Panel: Pre-seed the backlog_index with representative mock Jira tickets.
    Generates text-embedding-004 vectors for each ticket to enable similarity matching.
    """
    tickets = [
        {
            "id": "PROJ-101",
            "title": "Login button styling and brand guidelines",
            "description": (
                "Ensure the login button is styled according to brand guidelines. "
                "In dark mode, it should use the dark grey color code #333333 and light text. "
                "On mobile views, the login button should be centered and full-width."
            )
        },
        {
            "id": "PROJ-102",
            "title": "Implement dark mode theme across mobile views",
            "description": (
                "Apply dark mode styling guidelines to all mobile and responsive views, "
                "ensuring accessible contrast ratios (WCAG AA minimum) for primary action buttons. "
                "Dark background: #1A1A1A, text: #F5F5F5, primary CTA: #4F46E5."
            )
        },
        {
            "id": "PROJ-103",
            "title": "Two-Factor Authentication (2FA) Implementation",
            "description": (
                "Build two-factor authentication flow supporting authenticator app TOTP (RFC 6238) "
                "and SMS-based verification via Twilio. User must be able to enroll, verify, "
                "and revoke 2FA devices. Targeted release is scheduled for end of August (P0)."
            )
        },
        {
            "id": "PROJ-104",
            "title": "Session Management and Cookie Timeout",
            "description": (
                "Set session cookie timeout to 15 minutes of inactivity. "
                "When the session times out, automatically redirect the user to the login screen. "
                "Do not expire sessions during active typing or API calls."
            )
        },
        {
            "id": "PROJ-105",
            "title": "Payment Flow Refactor — Stripe Integration",
            "description": (
                "Refactor the existing payment flow to use Stripe Elements for PCI-compliant card capture. "
                "Remove legacy PayPal integration. Support: credit/debit cards, Apple Pay, Google Pay. "
                "All payment errors must display user-friendly messages within 500ms."
            )
        },
    ]
    
    seeded = []
    errors = []
    
    for ticket in tickets:
        try:
            logger.info(f"Generating text-embedding-004 vector for {ticket['id']}...")
            combined_text = f"Title: {ticket['title']}\nDescription: {ticket['description']}"
            vector = get_embedding(combined_text)
            vector_str = f"[{','.join(map(str, vector))}]"
            
            with get_db_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO backlog_index (id, title, description, ticket_vector, last_synced_at, external_url)
                    VALUES (%s, %s, %s, %s, NOW(), %s)
                    ON CONFLICT (id) DO UPDATE 
                    SET title = EXCLUDED.title, 
                        description = EXCLUDED.description, 
                        ticket_vector = EXCLUDED.ticket_vector, 
                        last_synced_at = NOW()
                    """,
                    (
                        ticket["id"],
                        ticket["title"],
                        ticket["description"],
                        vector_str,
                        f"https://jira.company.com/browse/{ticket['id']}"
                    )
                )
            seeded.append(ticket["id"])
            logger.info(f"Seeded {ticket['id']} successfully")
            
        except Exception as e:
            logger.error(f"Failed to seed {ticket['id']}: {e}")
            errors.append({"ticket_id": ticket["id"], "error": str(e)})
    
    return {
        "status": "completed",
        "seeded_tickets": seeded,
        "errors": errors,
        "total": len(tickets),
        "successful": len(seeded)
    }

# ─────────────────────────────────────────────────────────────────────────────
# Dashboard API Endpoints (FR-08: Human-in-the-loop)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/dashboard/stats", tags=["dashboard"])
def get_dashboard_stats():
    """
    Returns aggregate stats for the dashboard stats bar.
    """
    with get_db_cursor() as cur:
        # Pending Review count
        cur.execute("SELECT COUNT(*) FROM extracted_requirements WHERE status = 'pending_review'")
        pending_review = cur.fetchone()["count"]
        
        # New Discoveries (unapproved new ticket actions)
        cur.execute(
            """
            SELECT COUNT(*) FROM reconciliation_actions 
            WHERE resolution_type = 'create_new_ticket' AND human_approved = FALSE
            """
        )
        new_discoveries = cur.fetchone()["count"]
        
        # Contradictions (unapproved conflict actions)
        cur.execute(
            """
            SELECT COUNT(*) FROM reconciliation_actions 
            WHERE resolution_type = 'contradiction_detected' AND human_approved = FALSE
            """
        )
        contradictions = cur.fetchone()["count"]
        
        # Resolved (ticket_created or archived)
        cur.execute(
            "SELECT COUNT(*) FROM extracted_requirements WHERE status IN ('ticket_created', 'archived')"
        )
        archived = cur.fetchone()["count"]
        
        # Total messages processed today
        cur.execute(
            """
            SELECT COUNT(*) FROM raw_messages 
            WHERE created_at >= CURRENT_DATE AND processing_status = 'completed'
            """
        )
        processed_today = cur.fetchone()["count"]
    
    return {
        "pendingReview": int(pending_review),
        "newDiscoveries": int(new_discoveries),
        "contradictions": int(contradictions),
        "archived": int(archived),
        "processedToday": int(processed_today)
    }


@app.get("/api/dashboard/actions", tags=["dashboard"])
def get_dashboard_actions():
    """
    Returns active reconciliation actions for the dashboard inbox.
    FR-08: No write operation without explicit human approval.
    """
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT 
                ra.id as action_id,
                ra.resolution_type,
                ra.similarity_score,
                ra.conflict_analysis,
                ra.suggested_ticket_draft,
                ra.human_approved,
                ra.created_at as action_created_at,
                er.id as requirement_id,
                er.extracted_text as requirement_text,
                er.is_hard_constraint,
                er.confidence_score,
                er.status as requirement_status,
                er.created_at as requirement_created_at,
                bi.id as closest_ticket_id,
                bi.title as closest_ticket_title,
                bi.description as closest_ticket_description,
                bi.external_url as closest_ticket_url,
                rm.source_channel,
                rm.author_identity
            FROM reconciliation_actions ra
            JOIN extracted_requirements er ON ra.requirement_id = er.id
            LEFT JOIN backlog_index bi ON ra.closest_ticket_id = bi.id
            LEFT JOIN raw_messages rm ON er.raw_message_id = rm.id
            WHERE ra.human_approved = FALSE 
              AND er.status NOT IN ('archived', 'ticket_created')
            ORDER BY ra.created_at DESC
            LIMIT 50
            """
        )
        rows = cur.fetchall()
    
    actions = []
    for r in rows:
        draft = None
        if r["suggested_ticket_draft"]:
            raw_draft = r["suggested_ticket_draft"]
            draft = json.loads(raw_draft) if isinstance(raw_draft, str) else raw_draft
        
        actions.append({
            "id": str(r["action_id"]),
            "type": "new_discovery" if r["resolution_type"] == "create_new_ticket" else "contradiction_alert",
            "slackMessage": r["requirement_text"],
            "suggestedTitle": draft.get("title") if draft else None,
            "suggestedDescription": draft.get("description") if draft else None,
            "acceptanceCriteria": draft.get("acceptanceCriteria") if draft else None,
            "conflictTicket": (
                f"{r['closest_ticket_id']}: {r['closest_ticket_title']}"
                if r["closest_ticket_id"] else None
            ),
            "conflictTicketUrl": r.get("closest_ticket_url"),
            "conflictAnalysis": r["conflict_analysis"],
            "similarityScore": float(r["similarity_score"]) if r["similarity_score"] else None,
            "isHardConstraint": r["is_hard_constraint"],
            "confidenceScore": float(r["confidence_score"]) if r["confidence_score"] else None,
            "requirementStatus": r["requirement_status"],
            "sourceChannel": r.get("source_channel"),
            "author": r.get("author_identity"),
            "createdAt": r["action_created_at"].isoformat() if r.get("action_created_at") else None,
        })
    
    return actions


@app.post("/api/dashboard/actions/{action_id}/approve", tags=["dashboard"])
def approve_action(
    action_id: str,
):
    """
    FR-08: Approve a reconciliation action without authentication.
    Enqueues the Jira write + audit log to ghost.approval Celery queue.
    """
    approved_by = "anonymous"

    # Validate action exists and is not already approved
    with get_db_cursor() as cur:
        cur.execute(
            "SELECT id, human_approved, resolution_type FROM reconciliation_actions WHERE id = %s",
            (action_id,)
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

    if row["human_approved"]:
        raise HTTPException(status_code=409, detail="Action is already approved")

    # Enqueue to ghost.approval queue
    approve_action_task.delay(action_id, approved_by)

    # Prometheus
    APPROVALS.labels(action="approved").inc()

    logger.info(f"Action approved (public): id={action_id} type={row['resolution_type']} by={approved_by}")
    return {
        "status": "approved_queued",
        "action_id": action_id,
        "resolution_type": row["resolution_type"],
        "approved_by": approved_by,
    }


@app.post("/api/dashboard/actions/{action_id}/dismiss", tags=["dashboard"])
def dismiss_action(
    action_id: str,
):
    """
    FR-08: Dismiss a reconciliation action — marks requirement as archived.
    No authentication required; any user can dismiss.
    """
    dismissed_by = "anonymous"

    with get_db_cursor() as cur:
        cur.execute(
            "SELECT id FROM reconciliation_actions WHERE id = %s",
            (action_id,)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

        # Mark requirement as archived
        cur.execute(
            """
            UPDATE extracted_requirements
            SET status = 'archived'
            WHERE id = (SELECT requirement_id FROM reconciliation_actions WHERE id = %s)
            """,
            (action_id,)
        )

        # Write a dismiss audit entry (anonymous)
        cur.execute(
            """
            INSERT INTO audit_log (action_id, actor_jwt_subject, action_payload)
            VALUES (%s, %s, %s)
            """,
            (
                action_id,
                dismissed_by,
                json.dumps({"action": "dismissed", "dismissed_by": dismissed_by, "timestamp": datetime.utcnow().isoformat()})
            )
        )

    # Prometheus
    APPROVALS.labels(action="dismissed").inc()

    logger.info(f"Action dismissed (public): id={action_id} by={dismissed_by}")
    return {"status": "dismissed", "action_id": action_id, "dismissed_by": dismissed_by}


@app.get("/api/dashboard/messages", tags=["dashboard"])
def get_recent_messages():
    """
    Returns recent raw messages and their processing status.
    """
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, source_channel, author_identity, processing_status, created_at
            FROM raw_messages
            ORDER BY created_at DESC
            LIMIT 20
            """
        )
        rows = cur.fetchall()
    
    return [
        {
            "id": str(r["id"]),
            "channel": r["source_channel"],
            "author": r["author_identity"],
            "status": r["processing_status"],
            "createdAt": r["created_at"].isoformat() if r.get("created_at") else None
        }
        for r in rows
    ]


@app.post("/api/dashboard/sync", tags=["dashboard"])
def trigger_backlog_sync():
    """
    FR-10: Trigger scheduled backlog sync.
    Re-seeds backlog index with current mock tickets.
    In production, this would pull from Jira/Linear REST API.
    """
    result = seed_backlog()
    return {
        "status": "sync_completed",
        "seeded": result["seeded_tickets"],
        "errors": result["errors"]
    }
