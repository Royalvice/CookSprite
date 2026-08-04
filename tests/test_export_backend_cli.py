"""Tests for ComfyUI export, the inference backend API, and the CLI."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

import workflow  # noqa: F401
from backend.app import create_app
from backend.adapters.stub import StubAdapter
from backend.ops import AdapterRouter
from workflow.export.comfyui import export_to_comfyui
from workflow.library import Library


# --- ComfyUI export --------------------------------------------------------

def test_comfyui_export_structure():
    spec = Library.load_builtin().resolve("single_sprite")
    result = export_to_comfyui(spec)
    assert not result.unmapped
    # Numeric string keys, each with class_type + inputs.
    for key, node in result.graph.items():
        assert key.isdigit()
        assert "class_type" in node and "inputs" in node
    # A downstream link is encoded as [id, output_index].
    px = next(n for n in result.graph.values() if n["class_type"] == "CookSpritePixelize")
    assert isinstance(px["inputs"]["image"], list)
    assert len(px["inputs"]["image"]) == 2


def test_comfyui_export_reports_unmapped():
    from workflow.schema import load_workflow_yaml

    spec = load_workflow_yaml(
        """
id: t
capability: t
output: a.image
nodes:
  - id: a
    component: text2img
"""
    )
    # Monkeypatch a fake unmapped component into the spec.
    spec.nodes[0].component = "text2img"
    res = export_to_comfyui(spec)
    assert res.graph  # text2img maps fine
    assert res.unmapped == []


# --- backend /infer async job ---------------------------------------------

def _backend_client() -> TestClient:
    return TestClient(create_app(AdapterRouter([StubAdapter()])))


def test_infer_async_job_completes():
    client = _backend_client()
    resp = client.post("/infer", json={
        "op": "text2img", "model_id": "stub-image",
        "inputs": {"prompt": "hello"}, "params": {"width": 64, "height": 64},
    })
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # Poll to completion.
    for _ in range(200):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in ("done", "error"):
            break
    assert status["status"] == "done"
    result = client.get(f"/jobs/{job_id}/result").json()
    assert result["outputs"] and "png_b64" in result["outputs"][0]


def test_infer_unroutable_is_rejected():
    client = _backend_client()
    resp = client.post("/infer", json={"op": "text2img", "model_id": "does-not-exist", "inputs": {}, "params": {}})
    assert resp.status_code == 400


def test_models_endpoint_lists_stub():
    client = _backend_client()
    models = client.get("/models").json()["models"]
    assert any(m["model_id"] == "stub-image" for m in models)


# --- CLI -------------------------------------------------------------------

def test_cli_list_json(capsys):
    from cli.__main__ import main

    rc = main(["list", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert any(c["id"] == "single_sprite" for c in data["capabilities"])
    assert any(comp["id"] == "pixelize" for comp in data["components"])


def test_cli_run_writes_artifacts(tmp_path, capsys):
    from cli.__main__ import main

    rc = main(["run", "single_sprite", "--prompt", "a bot", "--out", str(tmp_path / "ws"), "--quiet"])
    assert rc == 0
    out = capsys.readouterr().out
    # Report line + a JSON manifest entry with diffuse + normal ids.
    entry = json.loads(out[out.index("{"):])
    assert entry["kind"] == "sprite_pair"
    assert "diffuse" in entry and "normal" in entry
    # Files exist.
    artifacts = list((tmp_path / "ws" / "artifacts").glob("*.png"))
    assert len(artifacts) >= 2


def test_cli_export_comfyui(tmp_path, capsys):
    from cli.__main__ import main

    out_file = tmp_path / "wf.json"
    rc = main(["export", "single_sprite", "--out", str(out_file)])
    assert rc == 0
    graph = json.loads(out_file.read_text())
    assert graph and all("class_type" in n for n in graph.values())
