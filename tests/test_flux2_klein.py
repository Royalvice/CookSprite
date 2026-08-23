from __future__ import annotations

import tempfile

from fastapi.testclient import TestClient

from cooksprite.action_graphs import bind_action_task, materialize_recipe_workflows
from cooksprite.api.app import create_app
from cooksprite.recipes import (
    discover_recipes,
    model_bundle_status,
    recipe_contract_is_valid,
    recipe_for_model,
    recipe_variants,
    supports,
)
from cooksprite.store import Store


def _node(required: dict[str, str], output: list[str]) -> dict:
    return {
        "input": {"required": {name: [kind] for name, kind in required.items()}},
        "output": output,
    }


def _report(
    *, include_9b: bool = True, scale_node: str = "ImageScaleToTotalPixels"
) -> dict:
    diffusion = ["flux-2-klein-4b-fp8.safetensors"]
    encoders = ["qwen_3_4b.safetensors"]
    vaes = ["flux2-vae.safetensors"]
    if include_9b:
        diffusion.append("flux-2-klein-9b-fp8.safetensors")
        encoders.append("qwen_3_8b_fp8mixed.safetensors")
        vaes.append("full_encoder_small_decoder.safetensors")
    nodes = {
        "UNETLoader": _node({"unet_name": "COMBO", "weight_dtype": "COMBO"}, ["MODEL"]),
        "CLIPLoader": _node({"clip_name": "COMBO", "type": "COMBO"}, ["CLIP"]),
        "VAELoader": _node({"vae_name": "COMBO"}, ["VAE"]),
        "CLIPTextEncode": _node({"clip": "CLIP", "text": "STRING"}, ["CONDITIONING"]),
        "ConditioningZeroOut": _node(
            {"conditioning": "CONDITIONING"}, ["CONDITIONING"]
        ),
        "PrimitiveInt": _node({"value": "INT"}, ["INT"]),
        "EmptyFlux2LatentImage": _node(
            {"batch_size": "INT", "height": "INT", "width": "INT"}, ["LATENT"]
        ),
        "RandomNoise": _node({"noise_seed": "INT"}, ["NOISE"]),
        "KSamplerSelect": _node({"sampler_name": "COMBO"}, ["SAMPLER"]),
        "Flux2Scheduler": _node(
            {"height": "INT", "steps": "INT", "width": "INT"}, ["SIGMAS"]
        ),
        "CFGGuider": _node(
            {
                "cfg": "FLOAT",
                "model": "MODEL",
                "negative": "CONDITIONING",
                "positive": "CONDITIONING",
            },
            ["GUIDER"],
        ),
        "SamplerCustomAdvanced": _node(
            {
                "guider": "GUIDER",
                "latent_image": "LATENT",
                "noise": "NOISE",
                "sampler": "SAMPLER",
                "sigmas": "SIGMAS",
            },
            ["LATENT", "LATENT"],
        ),
        "VAEDecode": _node({"samples": "LATENT", "vae": "VAE"}, ["IMAGE"]),
        scale_node: _node(
            {
                "image": "IMAGE",
                "megapixels": "FLOAT",
                "resolution_steps": "INT",
                "upscale_method": "COMBO",
            },
            ["IMAGE"],
        ),
        "VAEEncode": _node({"pixels": "IMAGE", "vae": "VAE"}, ["LATENT"]),
        "ReferenceLatent": _node(
            {"conditioning": "CONDITIONING", "latent": "LATENT"}, ["CONDITIONING"]
        ),
    }
    nodes["UNETLoader"]["input"]["required"]["unet_name"] = [diffusion]
    nodes["CLIPLoader"]["input"]["required"]["clip_name"] = [encoders]
    nodes["CLIPLoader"]["input"]["required"]["type"] = [["flux2"]]
    nodes["VAELoader"]["input"]["required"]["vae_name"] = [vaes]
    return {
        "object_info": nodes,
        "models": {
            "diffusion_models": diffusion,
            "text_encoders": encoders,
            "vae": vaes,
        },
    }


def test_flux2_klein_uses_builtin_image_scale_when_total_pixel_node_is_unavailable():
    recipes = [
        item
        for item in discover_recipes(_report(scale_node="ImageScale"))
        if item.family == "comfy.flux2-klein"
    ]
    assert {item.id for item in recipes} == {
        "flux2-klein-4b-turbo-t2i",
        "flux2-klein-4b-turbo-i2i",
        "flux2-klein-9b-turbo-t2i",
        "flux2-klein-9b-turbo-i2i",
    }
    edit = next(item for item in recipes if item.id == "flux2-klein-9b-turbo-i2i")
    assert edit.workflow["scale_ref_1"]["class_type"] == "ImageScale"
    assert edit.workflow["scale_ref_1"]["inputs"]["width"] == 1024


def test_flux2_klein_discovers_complete_turbo_bundles_and_official_sampler_contract():
    recipes = [item for item in discover_recipes(_report()) if item.family == "comfy.flux2-klein"]
    assert {item.id for item in recipes} == {
        "flux2-klein-4b-turbo-t2i",
        "flux2-klein-4b-turbo-i2i",
        "flux2-klein-9b-turbo-t2i",
        "flux2-klein-9b-turbo-i2i",
    }
    for recipe in recipes:
        assert recipe_contract_is_valid(recipe)
        assert recipe.workflow["schedule"]["inputs"]["steps"] == 4
        assert recipe.workflow["guider"]["inputs"]["cfg"] == 1.0
        assert recipe.workflow["negative"]["class_type"] == "ConditioningZeroOut"
        assert recipe.workflow["negative"]["inputs"] == {"conditioning": ["positive", 0]}
        assert "negative" not in recipe.slots
        assert "negative" not in recipe.slot_types
    edit = next(item for item in recipes if item.id == "flux2-klein-9b-turbo-i2i")
    four = next(item for item in recipe_variants(edit) if item.workflow_variant == "i2i-4")
    assert {name for name in four.slots if name.startswith("reference_")} == {
        "reference_1",
        "reference_2",
        "reference_3",
        "reference_4",
    }
    assert sum(node["class_type"] == "ReferenceLatent" for node in four.workflow.values()) == 8
    assert supports(edit, "image.generate", {"reference": ["a", "b", "c", "d"]})
    assert not supports(edit, "image.generate", {"reference": ["a", "b", "c", "d", "e"]})


def test_flux2_i2i_workflow_variants_bind_one_to_four_artifacts():
    recipe = next(
        item for item in discover_recipes(_report()) if item.id == "flux2-klein-9b-turbo-i2i"
    )
    with tempfile.TemporaryDirectory() as directory:
        store = Store(directory)
        materialized = materialize_recipe_workflows(store, "rt_test", "snapshot", recipe)
        for count in range(1, 5):
            task, _, run_inputs, prompt_metadata = bind_action_task(
                store,
                "rt_test",
                "snapshot",
                materialized,
                "image.generate",
                {"reference": [f"artifact_{index}" for index in range(count)]},
                {"prompt": "edit", "count": 1, "resolution": 512},
            )
            assert len([name for name in run_inputs if name.startswith("reference_")]) == count
            assert len(task.nodes[0].inputs) >= count
            assert prompt_metadata["compiler_enabled"] is True


def test_model_identity_selects_t2i_or_i2i_recipe_from_inputs():
    recipes = [
        item for item in discover_recipes(_report()) if item.family == "comfy.flux2-klein"
    ]
    model_id = "flux-2-klein-9b-fp8.safetensors"
    assert recipe_for_model(recipes, model_id, "image.generate", {}).id.endswith("-t2i")
    assert recipe_for_model(
        recipes, model_id, "image.generate", {"reference": ["art_reference"]}
    ).id.endswith("-i2i")


def test_flux2_bundle_is_incomplete_when_one_loader_file_is_missing():
    report = _report()
    report["models"]["vae"].remove("full_encoder_small_decoder.safetensors")
    status = model_bundle_status(report, "flux2-klein-9b-turbo")
    assert not status["ready"]
    assert any(not item["present"] for item in status["files"])


def test_complete_9b_is_the_runtime_default_without_silent_4b_fallback(tmp_path):
    class FluxComfy:
        include_9b = True

        def __init__(self, _url: str):
            pass

        def doctor(self):
            return _report(include_9b=self.include_9b)

        def ping(self):
            return None

    app = create_app(tmp_path, FluxComfy)
    client = TestClient(app)
    assert client.post(
        "/api/v1/runtimes",
        json={"id": "rt_flux", "base_url": "http://flux", "location": "local"},
    ).status_code == 200
    assert client.post("/api/v1/runtimes/rt_flux/doctor").status_code == 200
    defaults = client.get("/api/v1/runtimes/rt_flux/defaults").json()
    assert defaults["defaults"]["image.generate"] == {
        "model_id": "flux-2-klein-9b-fp8.safetensors"
    }
    models = {item["id"]: item for item in defaults["models"]}
    assert models["flux-2-klein-9b-fp8.safetensors"]["modes"] == ["t2i", "i2i"]
    assert all(
        "T2I" not in item["label"] and "I2I" not in item["label"]
        for item in models.values()
    )

    FluxComfy.include_9b = False
    assert client.post("/api/v1/runtimes/rt_flux/doctor").status_code == 200
    defaults = client.get("/api/v1/runtimes/rt_flux/defaults").json()
    assert "image.generate" not in defaults["defaults"]
