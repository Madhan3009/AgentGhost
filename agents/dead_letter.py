"""
Ghost Requirement Agent — Dead Letter Queue (DLQ) Handler
=========================================================
Tasks that exhaust all Celery retries are routed here instead of
silently disappearing. Failure envelopes are persisted to the
`dead_letter_log` PostgreSQL table for inspection and alerting.

Usage (from a task's on_failure handler):
    from agents.dead_letter import route_to_dead_letter

    @app.task(bind=True, max_retries=5, ...)
    def my_task(self, ...):
        ...

    @my_task.on_failure
    def my_task_on_failure(exc, task_id, args, kwargs, einfo):
        if self.request.retries >= self.max_retries:
            route_to_dead_letter(
                task_name="my_task",
                task_args=args,
                task_kwargs=kwargs,
                exception=exc,
                traceback_str=str(einfo),
            )
"""
import json
import logging
import uuid
from datetime import datetime

from agents.celery_app import app
from agents.db import get_db_cursor

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DLQ Router — called from task on_failure handlers
# ─────────────────────────────────────────────────────────────────────────────

def route_to_dead_letter(
    task_name: str,
    task_args: list,
    task_kwargs: dict,
    exception: Exception,
    traceback_str: str,
) -> None:
    """
    Publish a structured failure envelope to the ghost.dead_letter queue.

    This should be called from a task's on_failure signal handler AFTER
    all retries are exhausted. The envelope is processed by
    `process_dead_letter_task` which persists it to the dead_letter_log table.

    Args:
        task_name:     Dotted task name (e.g. 'agents.tasks.process_ingestion_task')
        task_args:     Positional args the failed task was called with
        task_kwargs:   Keyword args the failed task was called with
        exception:     The exception that caused the final failure
        traceback_str: Full traceback string for debugging
    """
    envelope = {
        "task_name": task_name,
        "task_args": task_args if isinstance(task_args, list) else list(task_args),
        "task_kwargs": task_kwargs or {},
        "exception": str(exception),
        "exception_type": type(exception).__name__,
        "traceback": traceback_str,
        "failed_at": datetime.utcnow().isoformat(),
    }

    logger.error(
        f"[DLQ] Task exhausted retries — routing to dead_letter: "
        f"task={task_name} exception_type={envelope['exception_type']} "
        f"exception={str(exception)[:120]}"
    )

    try:
        process_dead_letter_task.apply_async(
            args=[envelope],
            queue="ghost.dead_letter",
        )
    except Exception as dispatch_err:
        # Last resort: log to stderr if even DLQ dispatch fails
        logger.critical(
            f"[DLQ] CRITICAL: Failed to dispatch to dead_letter queue: {dispatch_err}. "
            f"Original failure envelope: {json.dumps(envelope)}",
            exc_info=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# DLQ Consumer Task
# ─────────────────────────────────────────────────────────────────────────────

@app.task(
    bind=True,
    max_retries=0,             # DLQ tasks never retry
    queue="ghost.dead_letter",
    name="agents.dead_letter.process_dead_letter_task",
    ignore_result=True,
)
def process_dead_letter_task(self, failure_envelope: dict):
    """
    Dead Letter Queue consumer task.

    Persists the failure envelope to the `dead_letter_log` table so that:
    - Engineering teams can inspect and replay failed tasks
    - Prometheus can alert on dead letter accumulation
    - Audit trail is maintained for compliance

    The ghost.dead_letter worker should run with concurrency=1
    to avoid race conditions on the dead_letter_log table.
    """
    task_name = failure_envelope.get("task_name", "unknown")
    exception = failure_envelope.get("exception", "")

    logger.error(
        f"[DLQ] Processing dead letter: task={task_name} "
        f"exception_type={failure_envelope.get('exception_type', '?')} "
        f"exception={exception[:120]}"
    )

    try:
        with get_db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO dead_letter_log
                    (id, task_name, task_args, exception, exception_type, traceback, failed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    task_name,
                    json.dumps(failure_envelope.get("task_args", [])),
                    exception,
                    failure_envelope.get("exception_type", ""),
                    failure_envelope.get("traceback", ""),
                    failure_envelope.get("failed_at", datetime.utcnow().isoformat()),
                ),
            )
        logger.info(f"[DLQ] Dead letter envelope persisted to dead_letter_log for task={task_name}")

    except Exception as db_err:
        # Last-resort: log to stderr — can't do much else if DB is down too
        logger.critical(
            f"[DLQ] CRITICAL: Failed to persist dead letter envelope to DB: {db_err}. "
            f"Envelope: {json.dumps(failure_envelope)}",
            exc_info=True,
        )
