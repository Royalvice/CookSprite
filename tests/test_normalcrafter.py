from __future__ import annotations

import tempfile
import types

import numpy as np
from fastapi.testclient import TestClient

from cooksprite.action_graphs import (
    _normal_temporal_workflow,
    bind_action_task,
    materialize_recipe_workflows,
)
from cooksprite.api.app import create_app
from cooksprite.bridge import ArtifactBridge
from cooksprite.catalog import builtin_tools
from cooksprite.comfy.models import comfy_model_path
from cooksprite.compiler import Compiler
from cooksprite.nodes.normalcrafter.geometry import (
    prepare_rgba,
    restore_normal,
    spatial_transform,
)
from cooksprite.nodes.normalcrafter.nodes import CS_NormalCrafterBatch, CS_NormalCrafterSequence
from cooksprite.nodes.normalcrafter.runtime import _window_starts, register_model_folder
from cooksprite.recipes import LOTUS_NORMAL_NODES, discover_recipes, model_bundle_status
from cooksprite.store import Store
from cooksprite.workflows.lotus_normal import LOTUS_NORMAL_MODEL, LOTUS_NORMAL_VAE
from cooksprite.workflows.normalcrafter import (
    NORMALCRAFTER_BUNDLE,
    NORMALCRAFTER_BUNDLE_ID,
    NORMALCRAFTER_MODEL,
)


def _node(required: dict[str, str], output: list[str]) -> dict:
    return {
        "input": {"required": {name: [kind] for name, kind in required.items()}},
        "output": output,
    }


def _report(*, complete: bool = True) -> dict:
    files = NORMALCRAFTER_BUNDLE["files"]
    names = [f"{file['relative_path']}/{file['name']}" for file in files[:-1] if complete]
    if complete:
        names.append(f"{files[-1]['relative_path']}/{files[-1]['name']}")
    return {
        "object_info": {
            "CS_LoadArtifact": _node({"artifact_url": "STRING"}, ["IMAGE", "MASK", "CS_FRAMESEQ"]),
            "CS_StoreArtifact": _node({"upload_url": "STRING"}, ["STRING"]),
            "CS_NormalCrafterSequence": _node({"source": "CS_FRAMESEQ"}, ["CS_NORMAL_SEQUENCE"]),
            "CS_NormalCrafterBatch": _node({"image": "IMAGE"}, ["IMAGE", "MASK"]),
            "CS_Pixelize": _node({"image": "IMAGE"}, ["IMAGE", "MASK"]),
            "CS_PixelizePair": _node(
                {"image": "IMAGE", "normal": "IMAGE"}, ["IMAGE", "MASK", "IMAGE"]
            ),
        },
        "models": {"normalcrafter": names},
    }


def _report_with_lotus() -> dict:
    report = _report()
    report["object_info"].update(
        {name: _node({}, []) for name in LOTUS_NORMAL_NODES if name not in report["object_info"]}
    )
    report["models"].update(
        {
            "diffusion_models": [LOTUS_NORMAL_MODEL],
            "vae": [LOTUS_NORMAL_VAE],
        }
    )
    return report


class _DefaultsComfy:
    def __init__(self, _url: str):
        pass

    def doctor(self) -> dict:
        return {
            **_report_with_lotus(),
            "system_stats": {"system": {"comfyui_version": "test"}},
        }

    def ping(self) -> None:
        return None


def test_normalcrafter_node_contract_and_complete_bundle_discovery():
    assert CS_NormalCrafterSequence.RETURN_TYPES == ("CS_NORMAL_SEQUENCE",)
    assert CS_NormalCrafterBatch.RETURN_TYPES == ("IMAGE", "MASK")

    recipes = discover_recipes(_report())
    temporal = next(recipe for recipe in recipes if recipe.id == NORMALCRAFTER_BUNDLE_ID)
    sprite = next(
        recipe for recipe in recipes if recipe.id == "cooksprite-sprite-pixel-normalcrafter-v1"
    )
    assert temporal.checkpoint == NORMALCRAFTER_MODEL
    assert temporal.modes == ["frames-to-normal"]
    assert sprite.modes == ["frames-to-sprite-pair"]
    assert temporal.model_files == NORMALCRAFTER_BUNDLE["files"]
    assert not any(
        recipe.id == NORMALCRAFTER_BUNDLE_ID for recipe in discover_recipes(_report(complete=False))
    )

    status = model_bundle_status(_report(), NORMALCRAFTER_BUNDLE_ID)
    assert status["ready"] is True
    assert status["files"][0]["path"] == "models/normalcrafter/normalcrafter-v1/model_index.json"
    urls = [str(file["url"]) for file in NORMALCRAFTER_BUNDLE["files"]]
    revision = "7e24d68d86ae008fe08ef50b4e51cd2fc2c8cf57"
    assert urls[0].endswith(f"/resolve/{revision}/model_index.json")
    assert urls[3].endswith(f"/resolve/{revision}/image_encoder/model.fp16.safetensors")
    assert not any("/normalcrafter-v1/" in url for url in urls)


def test_normalcrafter_temporal_graph_is_typed_and_exposes_only_declared_knobs():
    recipe = next(
        recipe for recipe in discover_recipes(_report()) if recipe.id == NORMALCRAFTER_BUNDLE_ID
    )
    workflow = _normal_temporal_workflow("rt-test", recipe)
    assert workflow.inputs == {
        "source": "FrameSeq",
        "max_resolution": "Number",
        "window_size": "Number",
        "time_step_size": "Number",
        "decode_chunk_size": "Number",
        "strength": "Number",
        "flip_y": "Boolean",
    }
    node = workflow.nodes[0]
    assert node.tool == "cooksprite.normal_estimate_temporal"
    assert set(node.params) == {
        "max_resolution",
        "window_size",
        "time_step_size",
        "decode_chunk_size",
        "strength",
        "flip_y",
    }
    assert workflow.outputs["normal"].output == "normal"


def test_normalcrafter_temporal_recipe_compiles_to_the_standard_artifact_bridge():
    recipe = next(
        recipe for recipe in discover_recipes(_report()) if recipe.id == NORMALCRAFTER_BUNDLE_ID
    )
    with tempfile.TemporaryDirectory() as directory:
        store = Store(directory)
        materialized = materialize_recipe_workflows(store, "rt-test", "snapshot", recipe)
        task, workflows, inputs, _ = bind_action_task(
            store,
            "rt-test",
            "snapshot",
            materialized,
            "normal.generate",
            {"source": ["sequence-id"], "__source_kind": ["FrameSeq"]},
            {"strength": 1.0, "flip_y": False},
            {
                "max_resolution": 1024,
                "window_size": 14,
                "time_step_size": 10,
                "decode_chunk_size": 7,
            },
        )
        plan = Compiler(
            builtin_tools(),
            ArtifactBridge(b"k" * 32, "http://api.test/api/v1"),
            "run-normalcrafter",
        ).compile_task(task, workflows, inputs, {"step_1": task.nodes[0].candidates[0]})
    classes = [node["class_type"] for node in plan.graph.values()]
    assert classes.count("CS_LoadArtifact") == 1
    assert classes.count("CS_NormalCrafterSequence") == 1
    sink = next(node for node in plan.graph.values() if node["class_type"] == "CS_StoreArtifact")
    assert "normal_sequence" in sink["inputs"]
    assert "kind=NormalMap" in sink["inputs"]["upload_url"]


def test_normal_estimator_defaults_prefer_normalcrafter_but_allow_explicit_lotus(tmp_path):
    client = TestClient(create_app(tmp_path, _DefaultsComfy, allow_test_runtime=True))
    response = client.post(
        "/api/v1/runtimes",
        json={"id": "rt-test", "label": "Test", "base_url": "http://test"},
    )
    assert response.status_code == 200
    assert client.post("/api/v1/runtimes/rt-test/doctor").status_code == 200

    defaults = client.get("/api/v1/runtimes/rt-test/defaults").json()
    assert defaults["normal_estimators"] == {
        "single": {"model_id": LOTUS_NORMAL_MODEL},
        "temporal": {"model_id": NORMALCRAFTER_MODEL},
    }
    assert (
        client.put(
            "/api/v1/runtimes/rt-test/defaults/normal/temporal",
            json={"model_id": LOTUS_NORMAL_MODEL},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/runtimes/rt-test/defaults").json()["normal_estimators"][
        "temporal"
    ] == {"model_id": LOTUS_NORMAL_MODEL}
    generic = client.put(
        "/api/v1/runtimes/rt-test/defaults/normal.generate",
        json={"model_id": LOTUS_NORMAL_MODEL},
    )
    assert generic.status_code == 422
    assert generic.json()["detail"]["code"] == "normal_estimator_mode_required"


def test_normalcrafter_geometry_preserves_aspect_ratio_alpha_and_vector_normals():
    rgba = np.zeros((37, 91, 4), dtype=np.float32)
    rgba[..., :3] = (0.85, 0.2, 0.1)
    rgba[5:32, 9:82, 3] = 1.0
    prepared, alpha, transform = prepare_rgba(rgba, max_resolution=256)

    assert prepared.shape == (64, 128, 3)
    assert alpha.shape == rgba.shape[:2]
    assert transform.source_width == 91
    assert transform.source_height == 37
    assert transform.resized_width == 91
    assert transform.resized_height == 37
    assert prepared.dtype == np.float32

    prediction = np.zeros((*prepared.shape[:2], 3), dtype=np.float32)
    prediction[..., 2] = 1.0
    normal = restore_normal(prediction, alpha, transform, strength=1.0, flip_y=False)
    visible = alpha > 0.5
    vectors = normal[visible] * 2.0 - 1.0
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=-1), 1.0, atol=1e-5)
    np.testing.assert_allclose(
        normal[~visible],
        np.broadcast_to((0.5, 0.5, 1.0), normal[~visible].shape),
        atol=1e-6,
    )

    downscaled = spatial_transform(1920, 1080, max_resolution=1024)
    assert (downscaled.resized_width, downscaled.resized_height) == (1024, 576)
    assert (downscaled.padded_width, downscaled.padded_height) == (1024, 576)


def test_normalcrafter_window_schedule_never_emits_a_tail_overlap_twice():
    starts = _window_starts(100, window_size=14, step=10)
    assert starts[-2:] == [80, 86]
    emitted: list[int] = []
    for index, start in enumerate(starts):
        next_start = starts[index + 1] if index + 1 < len(starts) else 100
        emitted.extend(range(start, next_start))
    assert emitted == list(range(100))
    assert _window_starts(8, window_size=14, step=10) == [0]


def test_normalcrafter_registers_a_discoverable_comfy_model_folder(tmp_path, monkeypatch):
    seen: dict[str, tuple[str, bool]] = {}

    def add_model_folder_path(name: str, path: str, *, is_default: bool = False) -> None:
        seen[name] = (path, is_default)

    fake = types.SimpleNamespace(
        models_dir=str(tmp_path / "models"),
        folder_names_and_paths={},
        add_model_folder_path=add_model_folder_path,
    )
    monkeypatch.setitem(__import__("sys").modules, "folder_paths", fake)
    register_model_folder()
    assert seen == {"normalcrafter": (str(tmp_path / "models" / "normalcrafter"), True)}


def test_normalcrafter_nested_bundle_paths_cannot_collide(tmp_path):
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("# ComfyUI\n", encoding="utf-8")
    (root / "nodes.py").write_text("# ComfyUI\n", encoding="utf-8")
    (root / "comfy").mkdir()
    file = NORMALCRAFTER_BUNDLE["files"][-1]
    assert comfy_model_path(root, file) == (
        root
        / "models"
        / "normalcrafter"
        / "normalcrafter-v1"
        / "vae"
        / "diffusion_pytorch_model.safetensors"
    )
