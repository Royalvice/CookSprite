"""In-memory async job store for the inference backend.

`/infer` submits a job and returns immediately; the job runs in a thread (model
calls are blocking / CPU-or-GPU bound). Callers poll `/jobs/{id}` or subscribe
to progress. This is the async job model the contract requires (video is a
minutes-long task).

For multi-GPU / durable queues, Ray Serve (see serve.py) fronts this; the job
contract stays identical.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, work: Callable[["Job"], dict[str, Any]]) -> Job:
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job

        def runner() -> None:
            job.status = "running"
            try:
                job.result = work(job)
                job.progress = 1.0
                job.status = "done"
                job.message = "completed"
            except Exception as exc:  # surface, never silent-fallback
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = job.error

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
