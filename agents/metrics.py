"""
Ghost Requirement Agent — Prometheus Metrics
============================================
All custom counters, histograms, and gauges for pipeline observability.
These are exposed on the /metrics endpoint via prometheus-fastapi-instrumentator.

Labels follow the Prometheus naming conventions:
  - snake_case metric names
  - Prefix: ghost_
  - Units suffix: _total (counters), _seconds (latency), _bytes (size)
"""
from prometheus_client import Counter, Histogram, Gauge, Info

# ─────────────────────────────────────────────────────────────────────────────
# Build Info
# ─────────────────────────────────────────────────────────────────────────────

GHOST_INFO = Info(
    "ghost_agent",
    "Ghost Requirement Agent build information",
)
GHOST_INFO.info({
    "version": "1.0.0",
    "model": "gemini-2.5-flash",
    "embedding_model": "gemini-embedding-001",
})

# ─────────────────────────────────────────────────────────────────────────────
# Ingestion Pipeline Metrics
# ─────────────────────────────────────────────────────────────────────────────

MESSAGES_INGESTED = Counter(
    "ghost_messages_ingested_total",
    "Total raw messages received and queued for processing",
    ["source"],          # slack | teams | mock
)

MESSAGES_PROCESSED = Counter(
    "ghost_messages_processed_total",
    "Total messages fully processed by the Agno pipeline",
    ["status"],          # noise_filtered | pipeline_complete | skipped | failed
)

PIPELINE_DURATION = Histogram(
    "ghost_pipeline_duration_seconds",
    "End-to-end latency of the full Agno Team pipeline per message",
    buckets=[0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0, 30.0, 60.0],
)

# ─────────────────────────────────────────────────────────────────────────────
# Requirement Extraction Metrics
# ─────────────────────────────────────────────────────────────────────────────

REQUIREMENTS_EXTRACTED = Counter(
    "ghost_requirements_extracted_total",
    "Total requirements classified and routed by the pipeline",
    ["resolution_type", "is_hard_constraint"],
    # resolution_type: create_new_ticket | contradiction_detected | exact_match_found
    # is_hard_constraint: true | false
)

SIMILARITY_SCORES = Histogram(
    "ghost_similarity_score",
    "Distribution of cosine similarity scores between new requirements and backlog tickets",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
)

# ─────────────────────────────────────────────────────────────────────────────
# PII Masking Metrics
# ─────────────────────────────────────────────────────────────────────────────

PII_REDACTIONS = Counter(
    "ghost_pii_redactions_total",
    "Total PII elements redacted from messages before LLM submission",
    ["pii_type"],        # email | phone | ip
)

# ─────────────────────────────────────────────────────────────────────────────
# Human Approval Metrics
# ─────────────────────────────────────────────────────────────────────────────

APPROVALS = Counter(
    "ghost_approvals_total",
    "Total human review decisions on reconciliation actions",
    ["action"],          # approved | dismissed
)

PENDING_ACTIONS = Gauge(
    "ghost_pending_actions_current",
    "Current count of reconciliation actions awaiting human review",
)

# ─────────────────────────────────────────────────────────────────────────────
# PR Audit Metrics
# ─────────────────────────────────────────────────────────────────────────────

PR_AUDITS = Counter(
    "ghost_pr_audits_total",
    "Total GitHub pull request audits completed",
    ["status"],          # compliant | violations_flagged | failed
)

PR_VIOLATIONS = Counter(
    "ghost_pr_violations_total",
    "Total individual requirement violations detected in PR diffs",
)

PR_AUDIT_DURATION = Histogram(
    "ghost_pr_audit_duration_seconds",
    "Duration of a single PR audit (diff fetch + LLM analysis)",
    buckets=[1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0],
)

# ─────────────────────────────────────────────────────────────────────────────
# Dead Letter Queue Metrics
# ─────────────────────────────────────────────────────────────────────────────

DEAD_LETTERS = Counter(
    "ghost_dead_letters_total",
    "Total tasks routed to the dead letter queue after exhausting all retries",
    ["task_name"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Embedding Metrics
# ─────────────────────────────────────────────────────────────────────────────

EMBEDDINGS_GENERATED = Counter(
    "ghost_embeddings_generated_total",
    "Total embedding vectors generated via gemini-embedding-001",
    ["purpose"],         # requirement | query | backlog_seed
)

EMBEDDING_DURATION = Histogram(
    "ghost_embedding_duration_seconds",
    "Time taken to generate a single embedding vector",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)
