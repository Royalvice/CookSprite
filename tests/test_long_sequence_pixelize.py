from __future__ import annotations

import json
import time
import urllib.parse
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from cooksprite.api.app import create_app
from cooksprite.domain import (
    FrameSequenceManifest,
    FrameSequenceTemporal,
    PixelGeometryPlanManifest,
)
from cooksprite.nodes.pixel.plan import resolve_temporal_mode, source_order_sha256
from cooksprite.runtime_state import apply_runtime_event, initial_runtime_state
from cooksprite.workflows.lotus_normal import LOTUS_NORMAL_MODEL, LOTUS_NORMAL_VAE


def _node(required: dict[str, str], output: list[str]) -> dict:
    return {"input": {"required": {key: [value] for key, value in required.items()}}, "output": output}


def _nodes() -> dict[str, dict]:
    """Small capability report sufficient for the pixel and Lotus Recipes."""

    names = {
        "CS_LoadArtifact": _node({"artifact_url": "STRING"}, ["IMAGE", "MASK", "CS_FRAMESEQ", "CS_PIXEL_PLAN"]),
        "CS_StoreArtifact": {
            "input": {
                "required": {"upload_url": ["STRING"]},
                "optional": {
                    "value": ["IMAGE"],
                    "mask": ["MASK"],
                    "sequence": ["CS_PIXEL_SEQUENCE"],
                    "pixel_plan": ["CS_PIXEL_PLAN"],
                },
            },
            "output": ["STRING"],
        },
        "CS_Pixelize": _node({"image": "IMAGE"}, ["IMAGE", "MASK"]),
        "CS_PixelizeSequence": _node({"source": "CS_FRAMESEQ"}, ["CS_PIXEL_SEQUENCE", "CS_PIXEL_PLAN"]),
        "CS_LotusModelLoader": _node({"model_name": "STRING"}, ["MODEL"]),
        "CS_LotusNormalPrepare": _node({"image": "IMAGE"}, ["IMAGE", "MASK", "IMAGE"]),
        "CS_LotusNormalFinalize": _node({"prediction": "IMAGE", "reference": "IMAGE"}, ["IMAGE", "MASK"]),
        "CS_ProjectNormalToPixelPlan": _node({"source": "IMAGE", "normal": "IMAGE", "pixel_plan": "CS_PIXEL_PLAN"}, ["IMAGE", "MASK"]),
        "VAELoader": _node({"vae_name": "STRING"}, ["VAE"]),
        "VAEEncode": _node({"pixels": "IMAGE", "vae": "VAE"}, ["LATENT"]),
        "LotusConditioning": _node({}, ["CONDITIONING"]),
        "BasicGuider": _node({}, ["GUIDER"]),
        "DisableNoise": _node({}, ["NOISE"]),
        "BasicScheduler": _node({}, ["SIGMAS"]),
        "SetFirstSigma": _node({}, ["SIGMAS"]),
        "KSamplerSelect": _node({}, ["SAMPLER"]),
        "SamplerCustomAdvanced": _node({}, ["LATENT", "LATENT"]),
        "VAEDecode": _node({}, ["IMAGE"]),
    }
    return names


class LongSequenceComfy:
    """In-process Comfy contract double for artifact-bridge integration tests."""

    submitted: ClassVar[list[dict]] = []
    store = None

    def __init__(self, url: str):
        self.url = url
        self.graph: dict = {}

    def doctor(self) -> dict:
        return {
            "object_info": _nodes(),
            "models": {
                "diffusion_models": [LOTUS_NORMAL_MODEL],
                "vae": [LOTUS_NORMAL_VAE],
            },
            "system_stats": {"system": {"comfyui_version": "test"}},
        }

    def submit(self, graph: dict, *args, **kwargs) -> str:
        self.graph = graph
        self.__class__.submitted.append(graph)
        return "long-sequence-prompt"

    def ping(self) -> None:
        return None

    def queue(self) -> dict:
        return {"queue_running": [], "queue_pending": []}

    def cancel(self, prompt_id=None) -> None:
        return None

    @staticmethod
    def _query(url: str) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlparse(url)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    def _put_image(self, *, run_id: str, project_id: str, kind: str, source: str, index: int) -> None:
        payload = f"{kind}:{run_id}:{source}:{index}".encode()
        artifact = self.__class__.store.put_artifact(
            payload,
            "image/png",
            kind,
            {"source_artifacts": [source] if source else [], "output_index": index, "canvas": [16, 16]},
            project_id=project_id,
        )
        self.__class__.store.attach_run_artifact(run_id, artifact.id, allow_duplicate=True)

    def _put_plan(self, *, run_id: str, project_id: str, source_sequence: str) -> None:
        sequence = FrameSequenceManifest.model_validate_json(
            self.__class__.store.artifact_bytes(source_sequence)
        )
        source_frames = []
        for source_id in sequence.frames:
            row = self.__class__.store.artifact(source_id)
            assert row is not None
            source_frames.append(
                {"artifact": source_id, "sha256": row["sha256"], "canvas": [16, 16]}
            )
        plan = PixelGeometryPlanManifest(
            source_order_sha256=source_order_sha256(source_frames),
            frames=[
                {
                    "source_artifact": item["artifact"],
                    "source_sha256": item["sha256"],
                    "canvas": item["canvas"],
                }
                for item in source_frames
            ],
            canvas=(16, 16),
            transform={
                "mode": "shared",
                "value": {
                    "source_bbox_xyxy": [0, 0, 16, 16],
                    "target_size": [16, 16],
                    "padding_xy": [0, 0],
                    "scale": 1.0,
                    "draw_size_wh": [16, 16],
                    "offset_xy": [0.0, 0.0],
                },
            },
            target=(16, 16),
            padding=(0, 0),
            supersample=1,
            temporal_mode="flow",
            profile="fidelity",
            palette_budget=32,
            outline=False,
        )
        artifact = self.__class__.store.put_artifact(
            plan.model_dump_json(by_alias=True).encode(),
            "application/vnd.cooksprite.pixel-geometry-plan+json",
            "PixelGeometryPlan",
            {"system": True, "source_artifacts": [source_sequence]},
            project_id=project_id,
        )
        self.__class__.store.attach_run_artifact(run_id, artifact.id, allow_duplicate=True)

    def wait(self, prompt_id: str, *args, **kwargs) -> dict:
        sinks = [node for node in self.graph.values() if node["class_type"] == "CS_StoreArtifact"]
        for sink in sinks:
            _, query = self._query(sink["inputs"]["upload_url"])
            run_id = sink["inputs"]["upload_url"].split("/runs/", 1)[1].split("/", 1)[0]
            run = self.__class__.store.run(run_id)
            assert run is not None
            project_id = str(run["project_id"])
            kind = query["kind"][0]
            source = query.get("source_artifact", [""])[0]
            if "sequence" in sink["inputs"]:
                manifest = FrameSequenceManifest.model_validate_json(
                    self.__class__.store.artifact_bytes(source)
                )
                for index, source_id in enumerate(manifest.frames):
                    self._put_image(
                        run_id=run_id,
                        project_id=project_id,
                        kind="Image",
                        source=source_id,
                        index=index,
                    )
            elif "pixel_plan" in sink["inputs"]:
                self._put_plan(run_id=run_id, project_id=project_id, source_sequence=source)
            else:
                self._put_image(
                    run_id=run_id,
                    project_id=project_id,
                    kind=kind,
                    source=source,
                    index=0,
                )
        return {"status": {"completed": True}}


def _wait(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        view = client.get(f"/api/v1/runs/{run_id}").json()
        if view["status"] in {"succeeded", "failed", "cancelled"}:
            return view
        time.sleep(0.01)
    raise AssertionError("run did not finish")


def _client(tmp_path) -> TestClient:
    app = create_app(tmp_path, LongSequenceComfy, allow_test_runtime=True)
    LongSequenceComfy.store = app.state.store
    LongSequenceComfy.submitted = []
    client = TestClient(app)
    response = client.post(
        "/api/v1/runtimes",
        json={"id": "rt_sequence", "label": "Sequence Test", "base_url": "http://sequence-test"},
    )
    assert response.status_code == 200
    assert client.post("/api/v1/runtimes/rt_sequence/doctor").status_code == 200
    return client


def _sequence(client: TestClient, project_id: str, count: int = 2, *, fps: float | None = 12.0) -> tuple[dict, list[dict]]:
    store = client.app.state.store
    frames = [
        store.put_artifact(
            f"source-{index}".encode(),
            "image/png",
            "Image",
            {"canvas": [16, 16]},
            project_id=project_id,
        )
        for index in range(count)
    ]
    manifest = FrameSequenceManifest(
        frames=[frame.id for frame in frames],
        temporal=FrameSequenceTemporal(source="sampled_video", sample_fps=fps) if fps else None,
    )
    sequence = store.put_artifact(
        manifest.model_dump_json(by_alias=True, exclude_none=False).encode(),
        "application/vnd.cooksprite.frame-sequence+json",
        "FrameSeq",
        {"role": "frame_sequence", "frame_count": count},
        project_id=project_id,
    )
    return sequence.model_dump(mode="json"), [frame.model_dump(mode="json") for frame in frames]


def test_temporal_auto_only_uses_flow_for_sampled_video_at_or_above_eight_fps():
    assert resolve_temporal_mode("auto", {"source": "sampled_video", "sample_fps": 8}) == "flow"
    assert resolve_temporal_mode("auto", {"source": "sampled_video", "sample_fps": 7.99}) == "shared"
    assert resolve_temporal_mode("auto", None) == "shared"
    assert resolve_temporal_mode("shared", None) == "shared"
    with pytest.raises(ValueError, match="at least 8 FPS"):
        resolve_temporal_mode("flow", None)


def test_runtime_progress_labels_the_two_long_sequence_phases():
    graph = {"pixel": {"class_type": "CS_PixelizeSequence", "inputs": {}}}
    state, _ = apply_runtime_event(
        initial_runtime_state(),
        {"type": "progress", "data": {"node": "pixel", "value": 4, "max": 12}},
        graph,
    )
    assert state["message"] == "Analyzing geometry and palette · 4/6"
    state, _ = apply_runtime_event(
        state,
        {"type": "progress", "data": {"node": "pixel", "value": 10, "max": 12}},
        graph,
    )
    assert state["message"] == "Pixelizing sequence · 4/6"


def test_long_sequence_pixelization_streams_plan_and_reuses_it_for_one_normal_keyframe(tmp_path):
    client = _client(tmp_path)
    project = client.post("/api/v1/projects", json={"type": "character"}).json()
    sequence, frames = _sequence(client, project["id"], fps=12.0)

    run = client.post(
        "/api/v1/actions/image.pixelize/runs",
        json={"project": project["id"], "inputs": {"source": sequence["id"]}, "values": {"temporal_mode": "auto"}},
    )
    assert run.status_code == 202
    result = _wait(client, run.json()["id"])
    assert result["status"] == "succeeded"
    assert [artifact["kind"] for artifact in result["artifacts"]] == ["FrameSeq"]

    graph = LongSequenceComfy.submitted[-1]
    pixel = next(node for node in graph.values() if node["class_type"] == "CS_PixelizeSequence")
    assert pixel["inputs"]["temporal_mode"] == "auto"
    assert any("sequence" in node["inputs"] for node in graph.values() if node["class_type"] == "CS_StoreArtifact")
    assert any("pixel_plan" in node["inputs"] for node in graph.values() if node["class_type"] == "CS_StoreArtifact")

    store = client.app.state.store
    source_row = store.artifact(frames[0]["id"])
    assert source_row is not None
    source_meta = json.loads(source_row["meta"])
    plan_id = source_meta["latest_pixel_plan_artifact"]
    assert source_meta["latest_pixel_plan_frame_index"] == 0
    assert store.artifact(plan_id)["kind"] == "PixelGeometryPlan"
    assert plan_id not in {artifact["id"] for artifact in client.get("/api/v1/artifacts").json()}
    assert plan_id not in {artifact["id"] for artifact in client.get(f"/api/v1/projects/{project['id']}/artifacts").json()}
    project_manifest = json.loads((store.project_directory(project["id"]) / "project.json").read_text())
    assert plan_id not in {artifact["id"] for artifact in project_manifest["artifacts"]}

    normal = client.post(
        "/api/v1/actions/normal.generate/runs",
        json={
            "project": project["id"],
            "inputs": {"source": frames[0]["id"], "pixel_plan": plan_id},
            "values": {},
        },
    )
    assert normal.status_code == 202
    normal_result = _wait(client, normal.json()["id"])
    assert normal_result["status"] == "succeeded"
    assert [artifact["kind"] for artifact in normal_result["artifacts"]] == ["NormalMap"]
    normal_graph = LongSequenceComfy.submitted[-1]
    project_node = next(
        node for node in normal_graph.values() if node["class_type"] == "CS_ProjectNormalToPixelPlan"
    )
    assert project_node["inputs"]["pixel_plan"][1] == 3
    normal_meta = normal_result["artifacts"][0]["meta"]
    assert normal_meta["pixel_plan_artifact"] == plan_id
    assert normal_meta["paired_diffuses"] == [source_meta["latest_pixel_frame_artifact"]]


def test_long_sequence_rejects_more_than_240_frames_and_noncontinuous_forced_flow(tmp_path):
    client = _client(tmp_path)
    project = client.post("/api/v1/projects", json={"type": "character"}).json()
    hand_sequence, frames = _sequence(client, project["id"], fps=None)

    forced_flow = client.post(
        "/api/v1/actions/image.pixelize/runs",
        json={
            "project": project["id"],
            "inputs": {"source": hand_sequence["id"]},
            "values": {"temporal_mode": "flow"},
        },
    )
    assert forced_flow.status_code == 422
    assert forced_flow.json()["detail"]["code"] == "flow_requires_continuous_video"

    store = client.app.state.store
    oversized_manifest = FrameSequenceManifest(frames=[frames[0]["id"]] * 241)
    oversized = store.put_artifact(
        oversized_manifest.model_dump_json(by_alias=True, exclude_none=False).encode(),
        "application/vnd.cooksprite.frame-sequence+json",
        "FrameSeq",
        {"role": "frame_sequence", "frame_count": 241},
        project_id=project["id"],
    )
    response = client.post(
        "/api/v1/actions/image.pixelize/runs",
        json={
            "project": project["id"],
            "inputs": {"source": oversized.id},
            "values": {"temporal_mode": "shared"},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "sequence_too_large"


def test_pixel_plan_rejects_tampered_or_wrong_source_provenance(tmp_path):
    client = _client(tmp_path)
    project = client.post("/api/v1/projects", json={"type": "character"}).json()
    sequence, frames = _sequence(client, project["id"], fps=12.0)
    run = client.post(
        "/api/v1/actions/image.pixelize/runs",
        json={"project": project["id"], "inputs": {"source": sequence["id"]}, "values": {}},
    )
    assert run.status_code == 202
    assert _wait(client, run.json()["id"])["status"] == "succeeded"
    store = client.app.state.store
    source_row = store.artifact(frames[0]["id"])
    assert source_row is not None
    plan_id = json.loads(source_row["meta"])["latest_pixel_plan_artifact"]
    payload = json.loads(store.artifact_bytes(plan_id))
    payload["source_order_sha256"] = "0" * 64
    tampered = store.put_artifact(
        json.dumps(payload).encode(),
        "application/vnd.cooksprite.pixel-geometry-plan+json",
        "PixelGeometryPlan",
        {"system": True},
        project_id=project["id"],
    )
    response = client.post(
        "/api/v1/actions/normal.generate/runs",
        json={
            "project": project["id"],
            "inputs": {"source": frames[0]["id"], "pixel_plan": tampered.id},
            "values": {},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "pixel_plan_source_invalid"
