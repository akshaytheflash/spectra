"""Job tracking for long-running ingest/pipeline work (BackgroundTasks)."""

from __future__ import annotations

from typing import Any, Callable

from app.db.models import IngestJob, utcnow
from app.db.session import session_scope


class JobContext:
    """Mutable progress handle that persists to ``ingest_jobs``."""

    def __init__(self, job_id: str, total: int = 0) -> None:
        self.job_id = job_id
        self.total = total
        self.done = 0
        self._last_pct = -10
        self._log: list[str] = []

    def set_total(self, total: int) -> None:
        self.total = total
        self._save(message=self._log[-1] if self._log else "")

    def step(self, message: str = "", inc: int = 1) -> None:
        self.done += inc
        pct = int(100 * self.done / self.total) if self.total else 0
        if message:
            self._log.append(message)
        if pct >= self._last_pct + 5 or message:
            self._last_pct = pct
            self._save(message=message or (self._log[-1] if self._log else ""))

    def log(self, message: str) -> None:
        self._log.append(message)
        self._save(message=message)

    def _save(self, message: str) -> None:
        with session_scope() as db:
            job = db.get(IngestJob, self.job_id)
            if job:
                job.done = self.done
                job.total = self.total
                job.message = message[:1000]

    def finish(self, result: dict[str, Any], status: str = "completed", message: str = "") -> None:
        with session_scope() as db:
            job = db.get(IngestJob, self.job_id)
            if job:
                job.done = self.done or job.total
                job.status = status
                job.message = message or job.message
                job.result = result
                job.finished_at = utcnow()

    def fail(self, error: str) -> None:
        self.finish({"error": error}, status="failed", message=error[:1000])


def create_job(kind: str, params: dict[str, Any], total: int = 0) -> str:
    with session_scope() as db:
        job = IngestJob(kind=kind, params=params, total=total, status="running")
        db.add(job)
        db.flush()
        return job.id


def job_dict(job: IngestJob) -> dict:
    return {
        "id": job.id, "kind": job.kind, "status": job.status, "total": job.total, "done": job.done,
        "message": job.message, "result": job.result or {}, "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def run_in_background(fn: Callable[[JobContext], dict], job_id: str, total: int = 0) -> None:
    """Wrapper used by FastAPI BackgroundTasks."""
    ctx = JobContext(job_id, total)
    try:
        result = fn(ctx)
        ctx.finish(result)
    except Exception as exc:  # pragma: no cover - defensive
        ctx.fail(str(exc))
