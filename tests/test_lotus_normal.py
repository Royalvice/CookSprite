from __future__ import annotations

import inspect
import io

import numpy as np
from PIL import Image

from cooksprite.catalog import builtin_tools
from cooksprite.compiler import Compiler
from cooksprite.domain import ToolNode, ValueRef, WorkflowRevision
from cooksprite.nodes.cooksprite_nodes import (
    CS_LotusModelLoader,
    CS_LotusNormalFinalize,
    CS_LotusNormalPrepare,
    _lotus_normal_axes,
    _lotus_size,
    _png,
)
from cooksprite.recipes import discover_recipes, model_bundle_status
from cooksprite.workflows.lotus_normal import (
    LOTUS_NORMAL_BUNDLE,
    LOTUS_NORMAL_BUNDLE_ID,
    LOTUS_NORMAL_MODEL,
    LOTUS_NORMAL_VAE,
    lotus_normal_tool_graph,
)


def _node(required: dict[str, object], output: list[str]) -> dict:
    return {
        "input": {"required": {name: [kind] for name, kind in required.items()}},
        "output": output,
    }


def _report(*, complete: bool = True) -> dict:
    nodes = {
        "CS_LoadArtifact": _node({"artifact_url": "STRING"}, ["IMAGE", "MASK"]),
        "CS_StoreArtifact": _node({"value": "IMAGE"}, ["STRING"]),
        "CS_LotusModelLoader": _node({"model_name": "COMBO"}, ["MODEL"]),
        "CS_LotusNormalPrepare": _node({"image": "IMAGE"}, ["IMAGE", "MASK", "IMAGE"]),
        "CS_LotusNormalFinalize": _node(
            {"prediction": "IMAGE", "reference": "IMAGE"}, ["IMAGE", "MASK"]
        ),
        "UNETLoader": _node({"unet_name": [LOTUS_NORMAL_MODEL]}, ["MODEL"]),
        "VAELoader": _node({"vae_name": [LOTUS_NORMAL_VAE]}, ["VAE"]),
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
    vaes = [LOTUS_NORMAL_VAE] if complete else []
    nodes["VAELoader"] = _node({"vae_name": vaes}, ["VAE"])
    return {
        "object_info": nodes,
        "models": {"diffusion_models": [LOTUS_NORMAL_MODEL], "vae": vaes},
    }


def _workflow() -> WorkflowRevision:
    return WorkflowRevision(
        id="lotus-test",
        revision=1,
        runtime_id="rt-test",
        runtime_snapshot="snapshot",
        title="Lotus test",
        inputs={"source": "Image", "strength": "Number", "flip_y": "Boolean"},
        nodes=[
            ToolNode(
                id="normal",
                tool="cooksprite.normal_estimate",
                inputs={"image": ValueRef(input="source")},
                params={
                    "strength": ValueRef(input="strength"),
                    "flip_y": ValueRef(input="flip_y"),
                },
            )
        ],
        outputs={"normal": ValueRef(node="normal", output="normal")},
    )


def test_lotus_node_contract_replaces_legacy_node_and_keeps_alpha():
    assert CS_LotusModelLoader.RETURN_TYPES == ("MODEL",)
    assert CS_LotusNormalPrepare.RETURN_TYPES == ("IMAGE", "MASK", "IMAGE")
    assert CS_LotusNormalFinalize.RETURN_TYPES == ("IMAGE", "MASK")
    tool = next(item for item in builtin_tools() if item.id == "cooksprite.normal_estimate")
    assert [port.type for port in tool.outputs] == ["NormalMap", "Mask"]


def test_lotus_finalize_converts_camera_normals_to_sprite_tangent_space():
    raw = np.array([[[0.25, -0.5, 0.75]]], dtype=np.float32)

    nx, ny, nz = _lotus_normal_axes(raw, strength=2.0, flip_y=False)
    np.testing.assert_allclose(nx, [[-0.5]])
    np.testing.assert_allclose(ny, [[-1.0]])
    np.testing.assert_allclose(nz, [[0.75]])

    _, flipped_y, flipped_z = _lotus_normal_axes(raw, strength=1.0, flip_y=True)
    np.testing.assert_allclose(flipped_y, [[0.5]])
    np.testing.assert_allclose(flipped_z, [[0.75]])


def test_lotus_tangent_conversion_keeps_flat_normal_neutral():
    raw = np.array([[[0.0, 0.0, 1.0]]], dtype=np.float32)

    nx, ny, nz = _lotus_normal_axes(raw, strength=1.0, flip_y=False)

    np.testing.assert_allclose(nx, [[0.0]])
    np.testing.assert_allclose(ny, [[0.0]])
    np.testing.assert_allclose(nz, [[1.0]])


def test_normal_map_png_preserves_neutral_rgb_under_transparency():
    value = np.array([[[0.5, 0.5, 1.0]]], dtype=np.float32)
    transparent = np.zeros((1, 1), dtype=np.float32)

    normal = np.asarray(Image.open(io.BytesIO(_png(value, "NormalMap", transparent))))
    image = np.asarray(Image.open(io.BytesIO(_png(value, "Image", transparent))))

    assert normal[0, 0].tolist() == [128, 128, 255, 0]
    assert image[0, 0].tolist() == [0, 0, 0, 0]


def test_lotus_preprocess_sizes_preserve_orientation_and_multiple_of_eight():
    assert _lotus_size(512, 512) == (768, 768)
    assert _lotus_size(321, 777) == (320, 768)
    assert _lotus_size(999, 257) == (768, 200)
    for height, width in ((1, 31), (719, 1283), (2048, 64)):
        output = _lotus_size(height, width)
        assert max(output) == 768
        assert all(value >= 8 and value % 8 == 0 for value in output)


def test_lotus_sealed_graph_retains_official_one_step_contract():
    spec = lotus_normal_tool_graph()
    graph = spec["workflow"]
    assert graph["model"]["inputs"] == {"model_name": LOTUS_NORMAL_MODEL}
    loader_source = inspect.getsource(CS_LotusModelLoader.load)
    assert "torch.bfloat16" in loader_source
    assert "float8" not in loader_source
    assert graph["scheduler"]["inputs"] == {
        "model": ["model", 0],
        "scheduler": "normal",
        "steps": 1,
        "denoise": 1.0,
    }
    assert graph["sigma"]["inputs"]["sigma"] == 999.0000000000002
    assert graph["sampler"]["inputs"]["sampler_name"] == "euler"
    assert all(node["class_type"] != "ImageInvert" for node in graph.values())
    assert {"model", "vae", "conditioning", "guider"}.issubset(spec["shared_nodes"])


def test_lotus_sealed_graph_shares_model_and_vae_across_frames():
    compiler = Compiler(builtin_tools())
    workflow = _workflow()
    inputs = {"source": "image-a", "strength": 1.0, "flip_y": False}
    compiler.workflow(workflow, inputs)
    compiler.workflow(workflow, {**inputs, "source": "image-b"})
    classes = [node["class_type"] for node in compiler.graph.values()]
    assert classes.count("CS_LotusModelLoader") == 1
    assert classes.count("VAELoader") == 1
    assert classes.count("CS_LotusNormalPrepare") == 2
    assert classes.count("CS_LotusNormalFinalize") == 2


def test_lotus_recipe_requires_the_complete_official_bundle():
    recipes = discover_recipes(_report())
    recipe = next(item for item in recipes if item.id == LOTUS_NORMAL_BUNDLE_ID)
    assert recipe.actions == ["normal.generate"]
    assert recipe.checkpoint == LOTUS_NORMAL_MODEL
    assert recipe.model_files == LOTUS_NORMAL_BUNDLE["files"]
    assert not any(
        item.id == LOTUS_NORMAL_BUNDLE_ID for item in discover_recipes(_report(complete=False))
    )
    status = model_bundle_status(_report(complete=False), LOTUS_NORMAL_BUNDLE_ID)
    assert not status["ready"]
    assert next(file for file in status["files"] if file["folder"] == "vae")["present"] is False
