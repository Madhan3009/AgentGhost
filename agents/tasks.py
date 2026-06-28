"""
Ghost Requirement Agent — Celery Task Definitions
==================================================
Celery is responsible ONLY for:
  1. Receiving raw messages from the ghost.ingestion queue (Slack/Teams webhooks)
  2. Persisting raw messages to PostgreSQL (raw_messages table)
  3. Running Agent 2 (embedding search) to pre-fetch backlog context
  4. Calling GhostRequirementTeam.run() — Agno Team orchestrates Agents 1 & 3
  5. Persisting the structured PipelineResult to PostgreSQL
  6. Handling retries (exponential backoff), dead-letter routing
  7. Human approval → Jira write + audit log (ghost.approval queue)

Agent orchestration (Agent 1 → Agent 2 → Agent 3) is handled by Agno Team
in coordinate mode. Celery does NOT manually chain agent calls.

Queue Architecture (from PROJECT_CONTEXT.md):
  ghost.ingestion      (4 workers, 5 retries) — Entry: receives raw message + runs Agno Team
  ghost.approval       (2 workers, 5 retries) — Human approval → Jira write + audit log
  ghost.pr_analysis    (4 workers, 3 retries) — GitHub PR diff analysis (Phase 4)
  ghost.dead_letter    (1 worker, manual)     — Tasks exceeding max retries
"""
import uuid
import json
import random
import logging
import time
import httpx
from datetime import datetime

from agents.celery_app import app
from agents.db import get_db_cursor
from agents.embedding import get_embedding, search_similar_tickets
from agents.team import GhostRequirementTeam, build_pr_auditor_agent
from agents.pii import mask_pii_content
from agents.metrics import (
    MESSAGES_PROCESSED,
    REQUIREMENTS_EXTRACTED,
    PIPELINE_DURATION,
    PR_AUDITS,
    PR_VIOLATIONS,
    PR_AUDIT_DURATION,
    DEAD_LETTERS,
    SIMILARITY_SCORES,
)

logger = logging.getLogger(__name__)

import celery

class GhostDLQTask(celery.Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        from agents.dead_letter import route_to_dead_letter
        if self.name == "agents.tasks.process_ingestion_task":
            MESSAGES_PROCESSED.labels(status="failed").inc()
        DEAD_LETTERS.labels(task_name=self.name).inc()
        route_to_dead_letter(
            task_name=self.name,
            task_args=list(args) if args else [],
            task_kwargs=kwargs or {},
            exception=exc,
            traceback_str=str(einfo),
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)

# ─────────────────────────────────────────────────────────────────────────────
# Main Pipeline Task (ghost.ingestion queue)
# Celery entry point → runs the full Agno Team pipeline
# ─────────────────────────────────────────────────────────────────────────────

@app.task(
    bind=True,
    base=GhostDLQTask,
    max_retries=5,
    queue="ghost.ingestion",
    name="agents.tasks.process_ingestion_task",
)
def process_ingestion_task(self, message_id: str):
    """
    Main Celery entry point for the Ghost pipeline.

    Steps:
      1. Fetch raw message from PostgreSQL
      2. Run Agent 2 (embedding similarity search) to pre-fetch backlog context
      3. Invoke GhostRequirementTeam.run() — Agno Team handles Agent 1 + Agent 3
      4. Persist the PipelineResult to extracted_requirements + reconciliation_actions
      5. Mark raw message as completed

    Retry: exponential backoff 2^n seconds (2s, 4s, 8s, 16s, 32s)
    """
    logger.info(f"[Celery/Ingestion] Starting pipeline for message_id={message_id}")

    pipeline_start = time.monotonic()

    # ── Step 1: Fetch raw message ────────────────────────────────────────────
    with get_db_cursor() as cur:
        cur.execute(
            "SELECT raw_payload, source_channel, author_identity, timestamp "
            "FROM raw_messages WHERE id = %s",
            (message_id,),
        )
        row = cur.fetchone()
        if not row:
            logger.warning(f"[Celery/Ingestion] Message not found: {message_id}")
            return {"status": "skipped", "reason": "message_not_found"}

        raw_payload = row["raw_payload"]
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)
        raw_message_text = raw_payload.get("text", "").strip()
        message_text = mask_pii_content(raw_message_text)
        channel = row["source_channel"] or raw_payload.get("channel", "unknown")
        author = row["author_identity"] or raw_payload.get("user", "unknown")
        timestamp = str(row["timestamp"] or datetime.utcnow().isoformat())

        # Mark as processing
        cur.execute(
            "UPDATE raw_messages SET processing_status = 'processing' WHERE id = %s",
            (message_id,),
        )

    if not message_text:
        with get_db_cursor() as cur:
            cur.execute(
                "UPDATE raw_messages SET processing_status = 'completed' WHERE id = %s",
                (message_id,),
            )
        return {"status": "skipped", "reason": "empty_text"}

    try:
        # ── Step 2: Agent 2 — Embedding similarity search ───────────────────
        # Pre-fetch backlog context BEFORE calling the Agno Team.
        # This keeps the LLM coordinator focused on reasoning, not API calls.
        logger.info(f"[Celery/Ingestion] Running Agent 2 (embedding search) for: '{message_text[:50]}...'")
        similar_tickets = []
        backlog_context = ""
        top_similarity = 0.0
        closest_ticket_id = None

        try:
            with get_db_cursor() as cur:
                similar_tickets, backlog_context = search_similar_tickets(
                    requirement_text=message_text,
                    db_cursor=cur,
                    top_k=3,
                )
            if similar_tickets:
                top_similarity = similar_tickets[0]["similarity"]
                closest_ticket_id = similar_tickets[0]["id"]
        except Exception as embed_err:
            logger.warning(
                f"[Celery/Ingestion] Embedding search failed (continuing without context): {embed_err}"
            )
            backlog_context = "Backlog search unavailable. Treat as new discovery if classified as requirement."

        # ── Step 3: Agno Team — Agents 1 + 3 ───────────────────────────────
        logger.info(f"[Celery/Ingestion] Invoking GhostRequirementTeam (Agno coordinate mode)...")
        team = GhostRequirementTeam()
        result = team.run(
            message_text=message_text,
            channel=channel,
            author=author,
            timestamp=timestamp,
            backlog_context=backlog_context,
        )

        logger.info(
            f"[Celery/Ingestion] Agno Team result: is_requirement={result.is_requirement} "
            f"resolution={result.resolution_type} similarity={result.similarity_score}"
        )

        # ── Step 4: Persist results ──────────────────────────────────────────
        with get_db_cursor() as cur:
            if not result.is_requirement:
                # Noise filtered — no further storage needed
                logger.info(f"[Celery/Ingestion] Noise filtered: {message_id} — {result.rationale}")
                cur.execute(
                    "UPDATE raw_messages SET processing_status = 'completed' WHERE id = %s",
                    (message_id,),
                )
                MESSAGES_PROCESSED.labels(status="noise_filtered").inc()
                PIPELINE_DURATION.observe(time.monotonic() - pipeline_start)
                return {
                    "status": "noise_filtered",
                    "message_id": message_id,
                    "rationale": result.rationale,
                }

            # Determine final resolution type (respect exact match logic)
            final_resolution = result.resolution_type
            final_similarity = result.similarity_score if result.similarity_score is not None else top_similarity
            final_ticket_id = result.closest_ticket_id or closest_ticket_id

            # Override: only treat as exact match when similarity is near-verbatim (≥0.97).
            # Values 0.85–0.97 are semantically related but may carry different specifics
            # (e.g. different timeout values, changed deadlines) — these must go through
            # Agent 3 contradiction detection rather than being silently auto-resolved.
            if top_similarity >= 0.97 and final_resolution not in ("contradiction_detected", "create_new_ticket"):
                final_resolution = "exact_match_found"

            # Map resolution to requirement status
            status_map = {
                "exact_match_found": "ticket_created",
                "contradiction_detected": "conflict_flagged",
                "create_new_ticket": "pending_review",
            }
            req_status = status_map.get(final_resolution, "pending_review")

            # Insert extracted requirement
            requirement_id = uuid.uuid4()
            cur.execute(
                """
                INSERT INTO extracted_requirements
                    (id, raw_message_id, extracted_text, is_hard_constraint,
                     confidence_score, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    str(requirement_id),
                    message_id,
                    result.requirement_text,
                    result.is_hard_constraint,
                    result.confidence_score,
                    req_status,
                ),
            )

            # Generate and store embedding vector for the extracted requirement
            try:
                req_vector = get_embedding(result.requirement_text)
                vector_str = f"[{','.join(map(str, req_vector))}]"
                cur.execute(
                    "UPDATE extracted_requirements SET requirement_vector = %s WHERE id = %s",
                    (vector_str, str(requirement_id)),
                )
            except Exception as vec_err:
                logger.warning(f"[Celery/Ingestion] Could not store requirement vector: {vec_err}")

            # Build suggested_ticket_draft dict if present
            # Note: result.suggested_ticket_draft is Optional[str] (JSON string)
            draft_dict = None
            if result.suggested_ticket_draft:
                draft = result.suggested_ticket_draft
                if isinstance(draft, str):
                    try:
                        draft_dict = json.loads(draft)
                    except json.JSONDecodeError:
                        draft_dict = {"raw": draft}
                elif hasattr(draft, "model_dump"):
                    draft_dict = draft.model_dump()
                elif isinstance(draft, dict):
                    draft_dict = draft

            if not draft_dict and final_resolution == "create_new_ticket":
                # Fallback draft if Agent 3 didn't produce one
                draft_dict = {
                    "title": f"New requirement: {result.requirement_text[:80]}",
                    "description": result.requirement_text,
                    "acceptanceCriteria": [
                        f"Given the system is operational, "
                        f"When the feature is triggered, "
                        f"Then the requirement is satisfied: {result.requirement_text}"
                    ],
                    "components": ["General"],
                    "slackAttribution": {
                        "channel": channel,
                        "author": author,
                        "timestamp": timestamp,
                    },
                }

            # Insert reconciliation action (only if not noise)
            if final_resolution in ("exact_match_found", "contradiction_detected", "create_new_ticket"):
                action_id = uuid.uuid4()
                auto_approved = final_resolution == "exact_match_found"
                cur.execute(
                    """
                    INSERT INTO reconciliation_actions
                        (id, requirement_id, closest_ticket_id, similarity_score,
                         resolution_type, conflict_analysis, suggested_ticket_draft,
                         human_approved, approved_by, approved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(action_id),
                        str(requirement_id),
                        final_ticket_id,
                        final_similarity,
                        final_resolution,
                        result.conflict_analysis,
                        json.dumps(draft_dict) if draft_dict else None,
                        auto_approved,
                        "system_auto" if auto_approved else None,
                        datetime.utcnow() if auto_approved else None,
                    ),
                )

            # Mark raw message as completed
            cur.execute(
                "UPDATE raw_messages SET processing_status = 'completed' WHERE id = %s",
                (message_id,),
            )

        # Prometheus: record pipeline outcome
        MESSAGES_PROCESSED.labels(status="pipeline_complete").inc()
        PIPELINE_DURATION.observe(time.monotonic() - pipeline_start)
        if result.similarity_score is not None:
            SIMILARITY_SCORES.observe(float(result.similarity_score))
        REQUIREMENTS_EXTRACTED.labels(
            resolution_type=final_resolution,
            is_hard_constraint=str(result.is_hard_constraint).lower(),
        ).inc()

        logger.info(
            f"[Celery/Ingestion] Pipeline complete: requirement={requirement_id} "
            f"resolution={final_resolution} similarity={final_similarity:.4f}"
        )

        return {
            "status": "pipeline_complete",
            "message_id": message_id,
            "requirement_id": str(requirement_id),
            "resolution_type": final_resolution,
            "similarity_score": final_similarity,
            "is_hard_constraint": result.is_hard_constraint,
            "confidence_score": result.confidence_score,
        }

    except Exception as exc:
        logger.error(f"[Celery/Ingestion] Pipeline error for {message_id}: {exc}", exc_info=True)
        with get_db_cursor() as cur:
            cur.execute(
                "UPDATE raw_messages SET processing_status = 'failed' WHERE id = %s",
                (message_id,),
            )
        # Exponential backoff: 2, 4, 8, 16, 32 seconds
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1))



# ─────────────────────────────────────────────────────────────────────────────
# Human Approval Handler (ghost.approval queue, 2 workers, 5 retries)
# FR-08: Human-in-the-loop — Jira write ONLY after explicit approval
# ─────────────────────────────────────────────────────────────────────────────

@app.task(
    bind=True,
    base=GhostDLQTask,
    max_retries=5,
    queue="ghost.approval",
    name="agents.tasks.approve_action_task",
)
def approve_action_task(self, action_id: str, approved_by_user: str):
    """
    Human Approval Handler.

    FR-08: No write to Jira/Linear without explicit human approval.
    On approval:
      1. Mock-creates Jira ticket (adds to backlog_index for future similarity matching)
      2. Updates reconciliation_action: human_approved=TRUE, approved_by, approved_at
      3. Updates extracted_requirements: status='ticket_created'
      4. Writes immutable audit_log entry
    """
    logger.info(f"[Celery/Approval] Processing action={action_id} by={approved_by_user}")

    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT ra.resolution_type, ra.suggested_ticket_draft, ra.human_approved,
                   ra.requirement_id, er.extracted_text, er.requirement_vector
            FROM reconciliation_actions ra
            JOIN extracted_requirements er ON ra.requirement_id = er.id
            WHERE ra.id = %s
            """,
            (action_id,),
        )
        row = cur.fetchone()

        if not row:
            logger.warning(f"[Celery/Approval] Action not found: {action_id}")
            return {"status": "skipped", "reason": "action_not_found"}

        if row["human_approved"]:
            logger.info(f"[Celery/Approval] Already approved: {action_id}")
            return {"status": "skipped", "reason": "already_approved"}

        resolution_type = row["resolution_type"]
        raw_draft = row["suggested_ticket_draft"]
        if isinstance(raw_draft, dict):
            suggested_draft = raw_draft
        elif isinstance(raw_draft, str):
            try:
                suggested_draft = json.loads(raw_draft)
            except json.JSONDecodeError:
                suggested_draft = {"raw": raw_draft}
        else:
            suggested_draft = None
        requirement_id = row["requirement_id"]
        req_vector = row["requirement_vector"]
        req_text = row["extracted_text"]

    try:
        # Build ticket content from Agent 3's draft
        if resolution_type == "create_new_ticket" and suggested_draft:
            title = suggested_draft.get("title", "New Requirement")
            description = suggested_draft.get("description", req_text)
            ac_list = suggested_draft.get("acceptanceCriteria", [])
            if ac_list:
                description += "\n\n**Acceptance Criteria:**\n" + "\n".join(
                    f"- {ac}" for ac in ac_list
                )
            attribution = suggested_draft.get("slackAttribution", {})
        else:
            title = f"Resolution: {req_text[:80]}{'...' if len(req_text) > 80 else ''}"
            description = (
                f"Contradiction resolved by {approved_by_user}.\n\n"
                f"Original requirement: {req_text}"
            )
            attribution = {}

        # Create real Jira ticket via Atlassian Cloud REST API
        from agents.jira_client import create_issue
        from agents import config
        priority = suggested_draft.get("priority", "Medium") if (suggested_draft and isinstance(suggested_draft, dict)) else "Medium"
        jira_response = create_issue(
            title=title,
            description=description,
            priority=priority,
            labels=["ghost-agent", "auto-generated"],
        )
        jira_id = jira_response["key"]

        with get_db_cursor() as cur:
            # Write approved ticket to backlog_index with its vector for future matching
            cur.execute(
                """
                INSERT INTO backlog_index
                    (id, title, description, ticket_vector, last_synced_at, external_url)
                VALUES (%s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (id) DO UPDATE
                    SET title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        ticket_vector = EXCLUDED.ticket_vector,
                        last_synced_at = NOW()
                """,
                (
                    jira_id,
                    title,
                    description,
                    req_vector,
                    f"{config.JIRA_BASE_URL.rstrip('/')}/browse/{jira_id}",
                ),
            )

            # Mark action as human-approved
            cur.execute(
                """
                UPDATE reconciliation_actions
                SET human_approved = TRUE, approved_by = %s, approved_at = NOW()
                WHERE id = %s
                """,
                (approved_by_user, action_id),
            )

            # Update requirement status
            cur.execute(
                "UPDATE extracted_requirements SET status = 'ticket_created' WHERE id = %s",
                (requirement_id,),
            )

            # Write immutable audit log
            audit_payload = {
                "event": "ticket_created",
                "jira_ticket_id": jira_id,
                "jira_ticket_title": title,
                "resolution_type": resolution_type,
                "approved_by": approved_by_user,
                "approved_at": datetime.utcnow().isoformat(),
                "action_id": action_id,
                "slack_attribution": attribution,
            }
            cur.execute(
                """
                INSERT INTO audit_log (action_id, actor_jwt_subject, action_payload)
                VALUES (%s, %s, %s)
                """,
                (action_id, approved_by_user, json.dumps(audit_payload)),
            )

        logger.info(
            f"[Celery/Approval] Jira ticket created: {jira_id} for action={action_id}"
        )
        return {
            "status": "approved",
            "action_id": action_id,
            "jira_ticket_id": jira_id,
            "title": title,
        }

    except Exception as exc:
        logger.error(f"[Celery/Approval] Error processing {action_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1))



# ─────────────────────────────────────────────────────────────────────────────
# PR Analysis Task (ghost.pr_analysis queue — Phase 4 placeholder)
# ─────────────────────────────────────────────────────────────────────────────

@app.task(
    bind=True,
    base=GhostDLQTask,
    max_retries=3,
    queue="ghost.pr_analysis",
    name="agents.tasks.process_pr_analysis_task",
)
def process_pr_analysis_task(self, pr_payload: dict):
    """
    Phase 4: GitHub PR diff analysis against known requirements.

    Steps:
      1. Extract PR metadata (repo, number, title, diff_url)
      2. Fetch PR unified diff via GitHub API (falls back to mock diff if not configured)
      3. Retrieve candidate requirements from database matching PR context via embeddings
      4. Run Agent 4 (PR Auditor) to check for violations
      5. Save audit findings to database (pr_audits table)
    """
    pr_number = pr_payload.get("pr_number", "unknown")
    repo_name = pr_payload.get("repo_name", "unknown")
    title = pr_payload.get("title", "Mock Pull Request")
    diff_url = pr_payload.get("diff_url", "")
    diff_text = pr_payload.get("diff_text", "")

    logger.info(f"[Celery/PRAnalysis] Auditing PR #{pr_number} in repo {repo_name}")

    # ── Step 1: Fetch PR Diff ────────────────────────────────────────────────
    if not diff_text and diff_url:
        from agents.config import GITHUB_TOKEN
        try:
            logger.info(f"[Celery/PRAnalysis] Fetching pull request diff from: {diff_url}")
            headers = {"Accept": "application/vnd.github.v3.diff"}
            if GITHUB_TOKEN:
                headers["Authorization"] = f"token {GITHUB_TOKEN}"

            # If repo_name and pr_number are valid, use official API
            if repo_name != "unknown" and pr_number != "unknown":
                api_url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"
                resp = httpx.get(api_url, headers=headers, timeout=15.0)
                if resp.status_code == 200:
                    diff_text = resp.text
                else:
                    logger.warning(f"[Celery/PRAnalysis] API fetch failed ({resp.status_code}), falling back to direct diff_url...")
                    resp_direct = httpx.get(diff_url, timeout=15.0)
                    if resp_direct.status_code == 200:
                        diff_text = resp_direct.text
            else:
                resp_direct = httpx.get(diff_url, timeout=15.0)
                if resp_direct.status_code == 200:
                    diff_text = resp_direct.text
        except Exception as fetch_err:
            logger.error(f"[Celery/PRAnalysis] Error fetching PR diff: {fetch_err}")

    if not diff_text:
        logger.warning("[Celery/PRAnalysis] No diff content available. Falling back to default mock diff.")
        diff_text = (
            "diff --git a/src/config/session.js b/src/config/session.js\n"
            "index 83a2d78..d3210ef 100644\n"
            "--- a/src/config/session.js\n"
            "+++ b/src/config/session.js\n"
            "@@ -10,3 +10,3 @@\n"
            "-  // Session timeout in milliseconds (15 minutes)\n"
            "-  sessionTimeout: 15 * 60 * 1000,\n"
            "+  // Session timeout in milliseconds (40 minutes)\n"
            "+  sessionTimeout: 40 * 60 * 1000,\n"
        )

    # ── Step 2: Fetch Candidate Constraints ───────────────────────────────
    candidates = []
    try:
        # Generate embedding for PR title + start of diff
        query_text = f"Title: {title}\nDiff: {diff_text[:300]}"
        query_emb = get_embedding(query_text)

        with get_db_cursor() as cur:
            cur.execute(
                """
                SELECT id::text, extracted_text, is_hard_constraint
                FROM extracted_requirements
                ORDER BY requirement_vector <=> %s::vector
                LIMIT 5
                """,
                (query_emb,)
            )
            rows = cur.fetchall()
            for r in rows:
                candidates.append({
                    "id": r["id"],
                    "requirement_text": r["extracted_text"],
                    "is_hard_constraint": r["is_hard_constraint"]
                })
    except Exception as db_err:
        logger.error(f"[Celery/PRAnalysis] Error loading candidate requirements: {db_err}")

    if not candidates:
        logger.info("[Celery/PRAnalysis] Empty DB candidates. Loading testing/fallback constraints.")
        candidates = [
            {
                "id": "req-mock-123",
                "requirement_text": "Session timeout MUST be 15 minutes. This is a hard security constraint.",
                "is_hard_constraint": True
            },
            {
                "id": "req-mock-456",
                "requirement_text": "The login button MUST use color #1A73E8 on all mobile views.",
                "is_hard_constraint": True
            }
        ]

    req_context = ""
    for c in candidates:
        req_context += f"- Requirement [{c['id']}]: {c['requirement_text']} (Hard Constraint: {c['is_hard_constraint']})\n"

    # ── Step 3: Run Agent 4 (PR Auditor) ─────────────────────────────────────
    try:
        agent = build_pr_auditor_agent()
        prompt = f"""
        ## PR Metadata
        Title: {title}
        Number: {pr_number}
        Repository: {repo_name}

        ## Pull Request Diff
        {diff_text}

        ## Candidate Requirements
        {req_context}
        """

        logger.info(f"[Celery/PRAnalysis] Reviewing PR diff with PR Auditor...")
        response = agent.run(prompt)
        audit_result = response.content

        result_data = {}
        if audit_result:
            if hasattr(audit_result, "model_dump"):
                result_data = audit_result.model_dump()
            elif isinstance(audit_result, dict):
                result_data = audit_result
            else:
                result_data = json.loads(str(audit_result))
        else:
            result_data = {
                "status": "failed",
                "violations": [],
                "summary": "Agent returned empty response"
            }
    except Exception as agent_err:
        logger.error(f"[Celery/PRAnalysis] Agent run error: {agent_err}")
        result_data = {
            "status": "failed",
            "violations": [],
            "summary": f"Audit execution failed: {str(agent_err)}"
        }

    # ── Step 4: Persist Results to database ──────────────────────────────
    audit_id = uuid.uuid4()
    try:
        with get_db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO pr_audits (id, pr_number, repo_name, status, diff_snippet, findings, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    str(audit_id),
                    int(pr_number) if isinstance(pr_number, int) or str(pr_number).isdigit() else 0,
                    repo_name,
                    result_data.get("status", "failed"),
                    diff_text[:2000],
                    json.dumps(result_data)
                )
            )
        logger.info(f"[Celery/PRAnalysis] PR Audit saved to db: audit_id={audit_id}")
    except Exception as db_save_err:
        logger.error(f"[Celery/PRAnalysis] Error writing PR audit row: {db_save_err}")

    return {
        "status": "completed",
        "audit_id": str(audit_id),
        "pr_number": pr_number,
        "repo_name": repo_name,
        "audit_status": result_data.get("status"),
        "findings": result_data
    }


