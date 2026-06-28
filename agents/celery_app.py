import ssl
from celery import Celery
from kombu import Queue
from agents.config import REDIS_URL

app = Celery(
    'ghost_tasks',
    broker=REDIS_URL,
    backend=REDIS_URL,
    # Include both task modules so all tasks are discoverable by workers
    include=['agents.tasks', 'agents.dead_letter']
)

# Configure task routing to the specific queues required by the specification
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    broker_use_ssl={'ssl_cert_reqs': ssl.CERT_NONE} if REDIS_URL and REDIS_URL.startswith('rediss://') else None,
    redis_backend_use_ssl={'ssl_cert_reqs': ssl.CERT_NONE} if REDIS_URL and REDIS_URL.startswith('rediss://') else None,

    # ── Explicit queue declarations ───────────────────────────────────────────
    # All 6 queues from PROJECT_CONTEXT.md are declared so they appear in
    # Flower and workers can be started per-queue without auto-creation issues.
    task_queues=[
        Queue('ghost.ingestion'),
        Queue('ghost.embedding'),
        Queue('ghost.reconciliation'),
        Queue('ghost.approval'),
        Queue('ghost.pr_analysis'),
        Queue('ghost.dead_letter'),   # ← DLQ: receives tasks after final retry
    ],

    # ── Task routing ─────────────────────────────────────────────────────────
    task_routes={
        'agents.tasks.process_ingestion_task':          {'queue': 'ghost.ingestion'},
        'agents.tasks.generate_embedding_task':         {'queue': 'ghost.embedding'},
        'agents.tasks.reconcile_requirement_task':      {'queue': 'ghost.reconciliation'},
        'agents.tasks.approve_action_task':             {'queue': 'ghost.approval'},
        'agents.tasks.process_pr_analysis_task':        {'queue': 'ghost.pr_analysis'},
        'agents.dead_letter.process_dead_letter_task':  {'queue': 'ghost.dead_letter'},
    },

    # ── Reliability ───────────────────────────────────────────────────────────
    # ACK after task completes (not on receipt) — ensures at-least-once delivery.
    # If a worker crashes mid-task, the message is re-queued.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)
