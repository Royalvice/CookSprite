"""Provider-neutral live execution state for public Run views."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .domain import RunRuntimeState, RuntimeErrorView, RuntimeNodeView


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initial_runtime_state() -> dict[str, Any]:
    return RunRuntimeState(updated_at=_now()).model_dump(mode="json")


def _node_spec(graph: dict[str, dict[str, Any]], node_id: Any) -> dict[str, Any]:
    return graph.get(str(node_id), {}) if node_id is not None else {}


def _node_kind(class_type: str) -> str:
    value = class_type.lower()
    if any(
        token in value
        for token in ("store", "save", "preview", "loadartifact", "loadvideoartifact")
    ):
        return "artifact"
    if "conditioning" in value or "textencode" in value:
        return "conditioning"
    if any(token in value for token in ("checkpoint", "loader", "model", "clip", "vae")):
        return "model"
    if any(token in value for token in ("sampler", "diffusion", "denoise")):
        return "sampling"
    return "processing"


def _node_label(class_type: str) -> str:
    labels = {
        "CheckpointLoaderSimple": "Model loader",
        "CLIPTextEncode": "Text encoder",
        "KSampler": "Sampler",
        "EmptyLatentImage": "Latent canvas",
        "VAEDecode": "VAE decoder",
        "VAEEncode": "VAE encoder",
        "CS_LoadArtifact": "Load source",
        "CS_LoadVideoArtifact": "Load source video",
        "CS_StoreArtifact": "Store artifact",
    }
    if class_type in labels:
        return labels[class_type]
    clean = class_type.removeprefix("CS_").replace("_", " ")
    clean = re.sub(r"(?<!^)(?=[A-Z])", " ", clean)
    return " ".join(clean.split()).strip().title() or "ComfyUI node"


def _node_view(
    graph: dict[str, dict[str, Any]],
    node_id: Any,
    *,
    status: str = "executing",
    step: Any = None,
    total: Any = None,
    progress: Any = 0,
) -> dict[str, Any] | None:
    if node_id is None:
        return None
    spec = _node_spec(graph, node_id)
    class_type = str(spec.get("class_type") or "")
    kind = _node_kind(class_type) if class_type else "other"
    meta = spec.get("_meta") if isinstance(spec.get("_meta"), dict) else {}
    label = str(meta.get("title") or _node_label(class_type) if class_type else "ComfyUI node")
    try:
        current_step = int(step) if step is not None else None
    except (TypeError, ValueError):
        current_step = None
    try:
        current_total = int(total) if total is not None else None
    except (TypeError, ValueError):
        current_total = None
    try:
        ratio = float(progress)
    except (TypeError, ValueError):
        ratio = 0.0
    if current_total and current_step is not None and not progress:
        ratio = current_step / max(1, current_total)
    return {
        "label": label,
        "kind": kind,
        "status": status
        if status in {"queued", "executing", "cached", "completed", "failed"}
        else "executing",
        "step": current_step,
        "total": current_total,
        "progress": max(0.0, min(1.0, ratio)),
    }


def _node_id(data: dict[str, Any]) -> Any:
    return (
        data.get("node")
        or data.get("node_id")
        or data.get("display_node")
        or data.get("display_node_id")
    )


def _sequence_progress_message(
    graph: dict[str, dict[str, Any]], node_id: Any, value: float, maximum: float
) -> str | None:
    """Name the two streamed PixelSequence phases without exposing internals.

    ``CS_PixelizeSequence`` reports a single standard ComfyUI progress range:
    the first half scans shared geometry/palette and the second emits frames.
    The public Run state turns that into understandable product status while
    leaving the graph and raw Comfy event protocol untouched.
    """

    class_type = str(_node_spec(graph, node_id).get("class_type") or "")
    if class_type in {"CS_NormalCrafterSequence", "CS_NormalCrafterBatch"}:
        half = max(1, round(maximum / 2.0))
        if value <= half:
            return f"Preparing temporal normal inference · {int(value)}/{half}"
        return f"Inferring temporal normals · {int(value - half)}/{half}"
    if class_type != "CS_PixelizeSequence":
        return None
    half = max(1, round(maximum / 2.0))
    if value <= half:
        return f"Analyzing geometry and palette · {int(value)}/{half}"
    return f"Pixelizing sequence · {int(value - half)}/{half}"


def _phase(kind: str) -> str:
    return {
        "model": "loading_model",
        "conditioning": "processing",
        "sampling": "sampling",
        "artifact": "saving",
        "processing": "processing",
    }.get(kind, "processing")


def _error_code(message: str, fallback: str = "execution_error") -> str:
    value = message.lower()
    if "out of memory" in value or "cuda oom" in value or " vram" in value and "memory" in value:
        return "out_of_memory"
    if (
        "validation" in value
        or "required input" in value
        or "prompt" in value
        and "invalid" in value
    ):
        return "prompt_validation_error"
    return fallback


def _error_view(
    data: dict[str, Any],
    graph: dict[str, dict[str, Any]],
    *,
    code: str | None = None,
) -> dict[str, Any]:
    message = str(
        data.get("exception_message")
        or data.get("message")
        or data.get("error")
        or "ComfyUI execution failed"
    )
    node = _node_view(graph, _node_id(data), status="failed")
    detail = (
        data.get("traceback")
        or data.get("detail")
        or data.get("details")
        or data.get("node_errors")
        or data.get("extra_info")
    )
    if detail is not None and not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False, default=str)
    if isinstance(detail, str):
        detail = detail[:4000]
    result: dict[str, Any] = {
        "code": _error_code(message, code or "execution_error"),
        "message": message,
        "node": node["label"] if node else None,
        "type": str(data.get("exception_type") or data.get("type"))
        if data.get("exception_type") or data.get("type")
        else None,
        "detail": detail,
    }
    return {key: value for key, value in result.items() if value is not None}


def apply_runtime_event(
    previous: dict[str, Any] | None,
    event: dict[str, Any],
    graph: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], float | None]:
    """Reduce one ComfyUI websocket message to the stable Run state.

    The raw provider payload is intentionally consumed here and never leaves the
    private runtime adapter boundary.
    """

    state = RunRuntimeState.model_validate(previous or {})
    event_type = str(event.get("type") or "unknown")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    state.event = event_type
    state.updated_at = _now()
    progress: float | None = None

    if event_type == "status":
        queue_info = data.get("status") if isinstance(data.get("status"), dict) else {}
        exec_info = queue_info.get("exec_info") if isinstance(queue_info, dict) else {}
        remaining = exec_info.get("queue_remaining") if isinstance(exec_info, dict) else None
        if remaining is not None:
            try:
                state.queue_remaining = max(0, int(remaining))
            except (TypeError, ValueError):
                pass
        state.message = (
            f"ComfyUI queue · {state.queue_remaining} remaining"
            if state.queue_remaining is not None
            else "ComfyUI status received"
        )
    elif event_type == "execution_start":
        state.phase = "loading_model"
        state.model_status = "loading"
        state.message = "ComfyUI started · loading model"
    elif event_type == "execution_cached":
        nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
        state.cached_nodes = max(state.cached_nodes, len(nodes))
        if any(
            _node_kind(str(_node_spec(graph, node).get("class_type") or "")) == "model"
            for node in nodes
        ):
            state.model_status = "ready"
        state.message = f"ComfyUI · {len(nodes)} cached nodes"
    elif event_type == "executing":
        node = _node_view(graph, _node_id(data), status="executing")
        if node:
            state.current = RuntimeNodeView.model_validate(node)
            state.phase = _phase(node["kind"])
            if node["kind"] != "model" and state.model_status == "loading":
                state.model_status = "ready"
            state.message = node["label"]
        elif data.get("node") is None:
            state.phase = "completed"
            state.message = "ComfyUI execution completed"
    elif event_type == "progress":
        value = data.get("value") or 0
        maximum = max(1.0, float(data.get("max") or 1))
        progress = max(0.0, min(1.0, float(value) / maximum))
        node = _node_view(
            graph,
            _node_id(data),
            status="executing",
            step=value,
            total=data.get("max"),
            progress=progress,
        )
        if node:
            state.current = RuntimeNodeView.model_validate(node)
            state.phase = _phase(node["kind"])
            if node["kind"] != "model" and state.model_status == "loading":
                state.model_status = "ready"
            state.message = (
                _sequence_progress_message(graph, _node_id(data), float(value), maximum)
                or f"{node['label']} · {int(value)}/{int(maximum)}"
            )
        else:
            state.phase = "sampling"
            state.message = f"ComfyUI · {int(value)}/{int(maximum)}"
    elif event_type == "progress_state":
        nodes = data.get("nodes") if isinstance(data.get("nodes"), dict) else {}
        active = next(
            (
                item
                for item in nodes.values()
                if isinstance(item, dict) and item.get("state") in {"executing", "running"}
            ),
            None,
        )
        finished = sum(
            1
            for item in nodes.values()
            if isinstance(item, dict) and item.get("state") in {"finished", "completed", "cached"}
        )
        state.completed_nodes = max(state.completed_nodes, finished)
        if active:
            value = active.get("value") or 0
            maximum = max(1.0, float(active.get("max") or 1))
            progress = max(0.0, min(1.0, float(value) / maximum))
            node = _node_view(
                graph,
                _node_id(active),
                status="executing",
                step=value,
                total=active.get("max"),
                progress=progress,
            )
            if node:
                state.current = RuntimeNodeView.model_validate(node)
                state.phase = _phase(node["kind"])
                if node["kind"] != "model" and state.model_status == "loading":
                    state.model_status = "ready"
                state.message = (
                    _sequence_progress_message(graph, _node_id(active), float(value), maximum)
                    or f"{node['label']} · {int(value)}/{int(maximum)}"
                )
    elif event_type == "executed":
        node = _node_view(graph, _node_id(data), status="completed", progress=1)
        if node:
            state.current = RuntimeNodeView.model_validate(node)
            state.phase = _phase(node["kind"])
            state.completed_nodes += 1
            state.message = f"{node['label']} completed"
            if node["kind"] == "model":
                state.model_status = "ready"
    elif event_type == "execution_success":
        state.phase = "completed"
        state.model_status = "ready"
        state.message = "ComfyUI inference completed · collecting artifacts"
        progress = 1.0
    elif event_type == "execution_interrupted":
        state.phase = "cancelled"
        state.message = "ComfyUI execution interrupted"
    elif event_type == "execution_error" or "error" in event_type:
        state.phase = "failed"
        state.error = RuntimeErrorView.model_validate(_error_view(data, graph))
        state.message = state.error.message
        if state.model_status == "loading":
            state.model_status = "failed"
    elif event_type in {"logs", "log", "file_upload_failed"}:
        message = data.get("message") or data.get("log") or data.get("error")
        if message:
            state.message = str(message)

    return state.model_dump(mode="json"), progress


def runtime_state_for_exception(
    previous: dict[str, Any] | None,
    exc: Exception,
    graph: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = RunRuntimeState.model_validate(previous or {})
    details = getattr(exc, "details", {})
    if not isinstance(details, dict):
        details = {}
    message = str(exc) or exc.__class__.__name__
    code = str(getattr(exc, "code", "") or "")
    if not code or code == "comfy_error":
        code = _error_code(message, "runtime_execution_failed")
    error = _error_view({**details, "message": message}, graph, code=code)
    state.event = "error"
    state.phase = "cancelled" if code == "execution_interrupted" else "failed"
    state.message = message
    state.error = RuntimeErrorView.model_validate(error)
    state.updated_at = _now()
    if state.phase == "failed" and state.model_status == "loading":
        state.model_status = "failed"
    return state.model_dump(mode="json"), error


def terminal_runtime_state(
    previous: dict[str, Any] | None,
    *,
    phase: str,
    message: str,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = RunRuntimeState.model_validate(previous or {})
    state.event = phase
    state.phase = phase if phase in {"completed", "failed", "cancelled"} else "unknown"
    state.message = message
    state.error = RuntimeErrorView.model_validate(error) if error else None
    state.updated_at = _now()
    if phase == "completed":
        state.model_status = "ready"
    return state.model_dump(mode="json")


__all__ = [
    "apply_runtime_event",
    "initial_runtime_state",
    "runtime_state_for_exception",
    "terminal_runtime_state",
]
