"""Tracked background execution for durable CookSprite Runs."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock
from typing import Any

from .execution import ExecutionPlan
from .store import Store

TerminalCallback = Callable[[], None]
FailureCallback = Callable[[Exception], dict[str, Any]]


class RunSupervisor:
    """Own worker lifetime and normalize ComfyUI progress into public Runs."""

    def __init__(
        self,
        store: Store,
        comfy_factory: Callable[[str], Any],
        invalidate_runtime: Callable[[str], None],
        max_workers: int = 4,
    ) -> None:
        self.store = store
        self.comfy_factory = comfy_factory
        self.invalidate_runtime = invalidate_runtime
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cspr-run")
        self.futures: dict[str, Future[Any]] = {}
        self.lock = RLock()

    def _track(self, run_id: str, future: Future[Any]) -> None:
        with self.lock:
            self.futures[run_id] = future

        def done(_: Future[Any]) -> None:
            with self.lock:
                self.futures.pop(run_id, None)

        future.add_done_callback(done)

    def submit_plan(
        self,
        run_id: str,
        runtime: dict[str, Any],
        plan: ExecutionPlan,
        on_complete: TerminalCallback | None = None,
    ) -> None:
        future = self.executor.submit(self._execute_plan, run_id, runtime, plan, on_complete)
        self._track(run_id, future)

    def resume_prompt(
        self,
        run_id: str,
        runtime: dict[str, Any],
        prompt_id: str,
        on_complete: TerminalCallback | None = None,
    ) -> None:
        future = self.executor.submit(self._observe_prompt, run_id, runtime, prompt_id, on_complete)
        self._track(run_id, future)

    def submit_job(
        self,
        run_id: str,
        job: TerminalCallback,
        failure: FailureCallback | None = None,
    ) -> None:
        def execute() -> None:
            try:
                job()
            except Exception as exc:  # noqa: BLE001 - normalize the worker boundary.
                detail = (
                    failure(exc)
                    if failure
                    else {
                        "code": "background_job_failed",
                        "message": str(exc),
                    }
                )
                self.store.update_run(
                    run_id,
                    status="failed",
                    message=detail.get("message", "background job failed"),
                    error=json.dumps(detail),
                )

        self._track(run_id, self.executor.submit(execute))

    def _execute_plan(
        self,
        run_id: str,
        runtime: dict[str, Any],
        plan: ExecutionPlan,
        on_complete: TerminalCallback | None,
    ) -> None:
        self.store.update_run(
            run_id,
            status="running",
            message="submitted to ComfyUI",
            progress=0.02,
        )
        client = self.comfy_factory(runtime["base_url"])
        client_id = getattr(client, "client_id", lambda: None)()
        try:
            try:
                prompt_id = client.submit(plan.graph, client_id=client_id)
            except TypeError:  # Protocol doubles and older adapters.
                prompt_id = client.submit(plan.graph)
            self.store.update_run(run_id, prompt_id=prompt_id, progress=0.05)
            self._wait(client, prompt_id, client_id, run_id)
        except Exception as exc:  # noqa: BLE001 - runtime errors share one API shape.
            self.invalidate_runtime(runtime["id"])
            self._fail_or_cancel(run_id, exc)
            return
        self._complete(run_id, on_complete)

    def _observe_prompt(
        self,
        run_id: str,
        runtime: dict[str, Any],
        prompt_id: str,
        on_complete: TerminalCallback | None,
    ) -> None:
        client = self.comfy_factory(runtime["base_url"])
        try:
            self._wait(client, prompt_id, None, run_id)
        except Exception as exc:  # noqa: BLE001 - runtime errors share one API shape.
            self.invalidate_runtime(runtime["id"])
            self._fail_or_cancel(run_id, exc)
            return
        self._complete(run_id, on_complete)

    def _wait(self, client: Any, prompt_id: str, client_id: str | None, run_id: str) -> None:
        def progress(value: float, node: str) -> None:
            self.store.update_run(
                run_id,
                progress=0.05 + 0.9 * max(0.0, min(1.0, value)),
                message=f"ComfyUI · {node}",
            )

        try:
            client.wait(prompt_id, progress=progress, client_id=client_id)
        except TypeError:  # Protocol doubles and older adapters.
            client.wait(prompt_id)

    def _complete(self, run_id: str, on_complete: TerminalCallback | None) -> None:
        record = self.store.run(run_id)
        if record and record["status"] in {"cancel_requested", "cancelled"}:
            self.store.update_run(run_id, status="cancelled", message="cancelled", progress=1)
            return
        try:
            if on_complete:
                on_complete()
            self.store.update_run(run_id, status="succeeded", progress=1, message="completed")
        except Exception as exc:  # noqa: BLE001 - finalization is part of the Run.
            self.store.update_run(
                run_id,
                status="failed",
                message="artifact finalization failed",
                error=json.dumps({"code": "artifact_finalization_failed", "message": str(exc)}),
            )

    def _fail_or_cancel(self, run_id: str, exc: Exception) -> None:
        record = self.store.run(run_id)
        if record and record["status"] == "cancel_requested":
            self.store.update_run(run_id, status="cancelled", message="cancelled", progress=1)
            return
        self.store.update_run(
            run_id,
            status="failed",
            message="ComfyUI execution failed",
            error=json.dumps({"code": "comfy_execution_failed", "message": str(exc)}),
        )

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)


__all__ = ["RunSupervisor"]
