from __future__ import annotations

import base64
import io
import json
import time
import urllib.parse
import zipfile
from typing import ClassVar

from fastapi.testclient import TestClient

from cooksprite.api.app import create_app
from cooksprite.nodes.cooksprite_nodes import CS_CompilePromptPacket
from cooksprite.prompting import (
    ImagePromptRequest,
    SpritePromptCompiler,
    VideoPromptRequest,
)
from cooksprite.recipes import Recipe, imported_recipe_is_compatible, supports
from cooksprite.registry import ACTION_IDS, ActionRegistry
from cooksprite.store import Store

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8Dwn4GBgYGJAQoAHgQCAf8pWmQAAAAASUVORK5CYII="
)


def node(required, output):
    return {
        "input": {"required": {name: [kind] for name, kind in required.items()}},
        "output": output,
    }


CORE_NODES = {
    "CheckpointLoaderSimple": node({"ckpt_name": "STRING"}, ["MODEL", "CLIP", "VAE"]),
    "CLIPTextEncode": node({"text": "STRING", "clip": "CLIP"}, ["CONDITIONING"]),
    "KSampler": node(
        {
            "model": "MODEL",
            "seed": "INT",
            "steps": "INT",
            "cfg": "FLOAT",
            "sampler_name": "STRING",
            "scheduler": "STRING",
            "positive": "CONDITIONING",
            "negative": "CONDITIONING",
            "latent_image": "LATENT",
            "denoise": "FLOAT",
        },
        ["LATENT"],
    ),
    "EmptyLatentImage": node({"width": "INT", "height": "INT", "batch_size": "INT"}, ["LATENT"]),
    "VAEEncode": node({"pixels": "IMAGE", "vae": "VAE"}, ["LATENT"]),
    "VAEDecode": node({"samples": "LATENT", "vae": "VAE"}, ["IMAGE"]),
    "RepeatLatentBatch": node({"samples": "LATENT", "amount": "INT"}, ["LATENT"]),
    "ImageScale": node(
        {
            "image": "IMAGE",
            "upscale_method": "STRING",
            "width": "INT",
            "height": "INT",
            "crop": "STRING",
        },
        ["IMAGE"],
    ),
    "CS_LoadArtifact": node({"artifact_url": "STRING"}, ["IMAGE"]),
    "CS_StoreArtifact": node({"value": "IMAGE", "upload_url": "STRING"}, ["STRING"]),
    "CS_IsolateOnGreen": node({"image": "IMAGE", "tolerance": "FLOAT"}, ["IMAGE"]),
    "CS_Pixelize": node(
        {
            "image": "IMAGE",
            "target_width": "INT",
            "target_height": "INT",
            "enabled": "BOOLEAN",
        },
        ["IMAGE"],
    ),
    "CS_CompilePromptPacket": node(
        {
            "action_id": "STRING",
            "prompt": "STRING",
            "category": "STRING",
            "style": "STRING",
            "animation": "STRING",
            "view": "STRING",
            "direction": "STRING",
            "task": "STRING",
            "mode": "STRING",
            "caption": "STRING",
            "action": "STRING",
            "camera_preset": "STRING",
            "orientation": "STRING",
            "facing": "STRING",
            "model": "STRING",
            "width": "INT",
            "height": "INT",
            "background": "STRING",
            "edit_instruction": "STRING",
            "negative_terms": "STRING",
        },
        ["STRING", "STRING", "STRING"],
    ),
    "CS_NormalEstimate": node(
        {"image": "IMAGE", "strength": "FLOAT", "flip_y": "BOOLEAN"}, ["IMAGE"]
    ),
    "CS_SliceSpriteSheet": node(
        {
            "image": "IMAGE",
            "columns": "INT",
            "rows": "INT",
            "frame_width": "INT",
            "frame_height": "INT",
            "margin": "INT",
            "spacing": "INT",
            "exclude_empty": "BOOLEAN",
        },
        ["IMAGE"],
    ),
    "CS_LoadVideoArtifact": node(
        {"video": "STRING", "sample_fps": "FLOAT", "max_frames": "INT"}, ["IMAGE"]
    ),
    "ImportedSampler": node({"prompt": "STRING"}, ["IMAGE"]),
}


class ProtocolComfy:
    submitted: ClassVar[list[dict]] = []
    store = None

    def __init__(self, url):
        self.url = url

    def doctor(self):
        return {
            "object_info": CORE_NODES,
            "models": {"checkpoints": ["test-model.safetensors"]},
            "system_stats": {"system": {"comfyui_version": "test"}},
        }

    def submit(self, graph):
        self.__class__.submitted.append(graph)
        self.graph = graph
        return "private-prompt"

    def ping(self):
        return None

    def wait(self, prompt_id):
        def batch_count(node_id):
            node = self.graph[str(node_id)]
            inputs = node["inputs"]
            if node["class_type"] == "EmptyLatentImage":
                return int(inputs.get("batch_size", 1))
            if node["class_type"] == "RepeatLatentBatch":
                return int(inputs.get("amount", 1))
            if node["class_type"] == "CS_SliceSpriteSheet":
                return max(1, int(inputs.get("rows", 1))) * max(1, int(inputs.get("columns", 1)))
            if node["class_type"] == "CS_LoadVideoArtifact":
                return int(inputs.get("max_frames", 1))
            upstream = {
                "CS_Pixelize": "image",
                "CS_IsolateOnGreen": "image",
                "VAEDecode": "samples",
                "KSampler": "latent_image",
                "VAEEncode": "pixels",
                "ImageScale": "image",
            }.get(node["class_type"])
            if upstream:
                value = inputs.get(upstream)
                if isinstance(value, list) and len(value) == 2:
                    return batch_count(value[0])
            for value in inputs.values():
                if isinstance(value, list) and len(value) == 2 and str(value[0]) in self.graph:
                    return batch_count(value[0])
            return 1

        sinks = [node for node in self.graph.values() if node["class_type"] == "CS_StoreArtifact"]
        for sink_index, sink in enumerate(sinks):
            parsed = urllib.parse.urlparse(sink["inputs"]["upload_url"])
            query = urllib.parse.parse_qs(parsed.query)
            run_id = parsed.path.split("/runs/", 1)[1].split("/", 1)[0]
            kind = query["kind"][0]
            source = query.get("source_artifact", [""])[0]
            count = batch_count(sink["inputs"]["value"][0])
            for index in range(count):
                artifact = self.__class__.store.put_artifact(
                    PNG + sink_index.to_bytes(2, "big") + index.to_bytes(2, "big"),
                    "image/png",
                    kind,
                    {"source_artifacts": [source] if source else []},
                )
                self.__class__.store.attach_run_artifact(
                    run_id,
                    artifact.id,
                    allow_duplicate=True,
                )
        return {"status": {"completed": True}}

    def queue(self):
        return {"queue_running": [], "queue_pending": []}

    def cancel(self, prompt_id=None):
        return None


def ready_client(tmp_path):
    app = create_app(tmp_path, ProtocolComfy, allow_test_runtime=True)
    ProtocolComfy.store = app.state.store
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/runtimes",
            json={"id": "rt_test", "label": "Protocol Test", "base_url": "http://test"},
        ).status_code
        == 200
    )
    assert client.post("/api/v1/runtimes/rt_test/doctor").status_code == 200
    return client


def wait(client, run_id):
    for _ in range(100):
        state = client.get(f"/api/v1/runs/{run_id}").json()
        if state["status"] in {"succeeded", "failed", "cancelled"}:
            return state
        time.sleep(0.01)
    raise AssertionError("run did not finish")


def test_runtime_doctor_returns_a_compact_summary_not_the_dynamic_node_catalog(tmp_path):
    client = ready_client(tmp_path)
    response = client.post("/api/v1/runtimes/rt_test/doctor")
    assert response.status_code == 200
    report = response.json()
    assert "tools" not in report
    assert report["tool_count"] == len(CORE_NODES)
    assert report["recipe_count"] == len(report["recipes"])
    assert report["models"] == {"checkpoints": 1}
    assert len(response.content) < 10_000


def test_registry_is_stable_bilingual_and_has_no_hidden_preset_surface():
    registry = ActionRegistry()
    actions = registry.list()
    assert tuple(item.id for item in actions) == ACTION_IDS
    assert all(set(item.i18n) == {"zh-CN", "en"} for item in actions)
    public = actions[0].model_dump(mode="json")
    assert "task" not in public
    assert "presets" not in public
    assert "prompt_pack" not in json.dumps(public)
    normal_types = registry.get("normal.generate").accepts["source"].type
    assert normal_types == ["Image", "FrameSeq", "SpriteSheet"]
    assert registry.get("animation.generate").accepts["character"].required is False


def test_animation_recipes_advertise_their_actual_text_and_image_modes():
    i2v = Recipe(
        id="i2v",
        label="I2V",
        family="video",
        actions=["animation.generate"],
        modes=["i2v"],
    )
    t2v = Recipe(
        id="t2v",
        label="T2V",
        family="video",
        actions=["animation.generate"],
        modes=["t2v"],
    )
    assert supports(i2v, "animation.generate", {"character": ["art_character"]})
    assert not supports(i2v, "animation.generate", {})
    assert supports(t2v, "animation.generate", {})
    assert not supports(t2v, "animation.generate", {"character": ["art_character"]})


def test_sheet_and_video_candidate_counts_are_compiled_inside_comfy(tmp_path):
    client = ready_client(tmp_path)
    project = client.post("/api/v1/projects", json={"type": "character"}).json()
    sheet = client.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "SpriteSheet"},
        content=PNG,
    ).json()
    run = client.post(
        "/api/v1/actions/sheet.slice/runs",
        json={
            "project": project["id"],
            "inputs": {"sheet": sheet["id"]},
            "values": {"rows": 3, "columns": 4},
        },
    )
    assert run.status_code == 202
    assert wait(client, run.json()["id"])["status"] == "succeeded"
    graph = ProtocolComfy.submitted[-1]
    slicer = next(node for node in graph.values() if node["class_type"] == "CS_SliceSpriteSheet")
    assert slicer["inputs"]["rows"] * slicer["inputs"]["columns"] == 12
    assert any(node["class_type"] == "CS_LoadArtifact" for node in graph.values())

    video = client.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "Video", "media_type": "video/mp4"},
        content=b"video",
    ).json()
    run = client.post(
        "/api/v1/actions/video.sample/runs",
        json={"project": project["id"], "inputs": {"video": video["id"]}, "values": {}},
    )
    assert run.status_code == 202
    assert wait(client, run.json()["id"])["status"] == "succeeded"
    graph = ProtocolComfy.submitted[-1]
    loader = next(node for node in graph.values() if node["class_type"] == "CS_LoadVideoArtifact")
    assert loader["inputs"]["max_frames"] == 48


def test_asset_type_and_style_are_compiled_as_comfy_prompt_packet_and_graph_policy(tmp_path):
    client = ready_client(tmp_path)
    project = client.post("/api/v1/projects", json={"type": "static"}).json()
    action = client.get("/api/v1/actions/image.generate").json()

    def run(category, style):
        response = client.post(
            "/api/v1/actions/image.generate/runs",
            json={
                "project": project["id"],
                "inputs": {},
                "values": {
                    "model": action["models"][0]["id"],
                    "category": category,
                    "style": style,
                    "count": 1,
                },
            },
        )
        assert response.status_code == 202
        assert wait(client, response.json()["id"])["status"] == "succeeded"
        return ProtocolComfy.submitted[-1]

    pixel_graph = run("weapon", "pixel")
    smooth_graph = run("terrain", "smooth")
    pixel_packet = next(
        node for node in pixel_graph.values() if node["class_type"] == "CS_CompilePromptPacket"
    )
    smooth_packet = next(
        node for node in smooth_graph.values() if node["class_type"] == "CS_CompilePromptPacket"
    )
    assert pixel_packet["inputs"]["category"] == "weapon"
    assert pixel_packet["inputs"]["style"] == "pixel"
    assert smooth_packet["inputs"]["category"] == "terrain"
    assert smooth_packet["inputs"]["style"] == "smooth"
    assert (
        next(node for node in pixel_graph.values() if node["class_type"] == "CS_Pixelize")[
            "inputs"
        ]["enabled"]
        is True
    )
    assert (
        next(node for node in smooth_graph.values() if node["class_type"] == "CS_Pixelize")[
            "inputs"
        ]["enabled"]
        is False
    )


def test_action_project_semantics_are_shared_by_every_api_client(tmp_path):
    client = ready_client(tmp_path)
    image_action = client.get("/api/v1/actions/image.generate").json()
    project = client.post("/api/v1/projects", json={"type": "static"}).json()
    response = client.post(
        "/api/v1/actions/image.generate/runs",
        json={
            "project": project["id"],
            "inputs": {},
            "values": {
                "model": image_action["models"][0]["id"],
                "category": "terrain",
                "style": "smooth",
                "count": 1,
            },
        },
    )
    assert response.status_code == 202
    terrain_run = wait(client, response.json()["id"])
    character_input = terrain_run["artifacts"][0]["id"]
    assert client.get(f"/api/v1/projects/{project['id']}").json()["type"] == "tileset"
    document = client.get(f"/api/v1/projects/{project['id']}/document").json()["document"]
    assert document["type"] == "tileset"
    assert document["tileset"]["tile_width"] == 32

    static_project = client.post("/api/v1/projects", json={"type": "static"}).json()
    animation_action = client.get("/api/v1/actions/animation.generate").json()
    response = client.post(
        "/api/v1/actions/animation.generate/runs",
        json={
            "project": static_project["id"],
            "inputs": {"character": character_input},
            "values": {
                "model": animation_action["models"][0]["id"],
                "action": "walk",
                "view": "level",
                "direction": "s",
                "count": 2,
            },
        },
    )
    assert response.status_code == 202
    assert client.get(f"/api/v1/projects/{static_project['id']}").json()["type"] == "character"
    document = client.get(f"/api/v1/projects/{static_project['id']}/document").json()["document"]
    assert document["type"] == "character"
    assert document["character"]["clips"] == []


def test_imported_image_recipe_requires_bridge_pixel_policy_and_receives_it(tmp_path):
    recipe = Recipe(
        id="imported-image",
        label="Imported image",
        family="custom.image",
        actions=["image.generate"],
        modes=["t2i"],
        workflow={"10": {"class_type": "ImportedSampler", "inputs": {"prompt": ""}}},
        slots={"text": "10.prompt"},
        output=["10", 0],
        source="imported",
    )
    report = {
        "object_info": {
            "ImportedSampler": {},
            "CS_StoreArtifact": {},
            "CS_Pixelize": {},
        }
    }
    assert imported_recipe_is_compatible(recipe, report)
    assert not imported_recipe_is_compatible(
        recipe,
        {"object_info": {"ImportedSampler": {}, "CS_StoreArtifact": {}}},
    )
    client = ready_client(tmp_path)
    imported = client.post(
        "/api/v1/runtimes/rt_test/recipes",
        json={
            key: value
            for key, value in recipe.dump().items()
            if key not in {"source", "runtime_snapshot", "workflows"}
        },
    )
    assert imported.status_code == 201
    project = client.post("/api/v1/projects", json={"type": "static"}).json()
    run = client.post(
        "/api/v1/actions/image.generate/runs",
        json={
            "project": project["id"],
            "inputs": {},
            "values": {
                "model": "rt_test:imported-image",
                "category": "character",
                "style": "pixel",
                "count": 1,
                "seed": -1,
            },
        },
    )
    assert run.status_code == 202
    assert wait(client, run.json()["id"])["status"] == "succeeded"
    graph = ProtocolComfy.submitted[-1]
    assert any(node["class_type"] == "ImportedSampler" for node in graph.values())
    assert any(node["class_type"] == "CS_Pixelize" for node in graph.values())


def test_action_request_compiles_to_real_comfy_graph_and_artifact_store(tmp_path):
    client = ready_client(tmp_path)
    project = client.post("/api/v1/projects", json={"type": "static"}).json()
    action = client.get("/api/v1/actions/image.generate").json()
    assert action["available"] is True
    assert action["models"] == [
        {
            "id": action["models"][0]["id"],
            "label": "Protocol Test · test-model.safetensors",
            "runtime_id": "rt_test",
            "family": "comfy.core-checkpoint",
            "modes": ["t2i", "i2i", "i2i-sequence"],
        }
    ]
    request = {
        "project": project["id"],
        "inputs": {},
        "values": {
            "style": "pixel",
            "category": "character",
            "prompt": "a soup knight",
            "model": action["models"][0]["id"],
            "count": 4,
        },
    }
    response = client.post("/api/v1/actions/image.generate/runs", json=request)
    assert response.status_code == 202
    state = wait(client, response.json()["id"])
    assert state["status"] == "succeeded"
    assert len(state["artifacts"]) == 4
    graph = ProtocolComfy.submitted[-1]
    assert any(node["class_type"] == "KSampler" for node in graph.values())
    assert any(node["class_type"] == "CS_StoreArtifact" for node in graph.values())
    packet = next(node for node in graph.values() if node["class_type"] == "CS_CompilePromptPacket")
    assert packet["inputs"]["prompt"] == "a soup knight"
    assert packet["inputs"]["category"] == "character"
    assert packet["inputs"]["style"] == "pixel"
    assert state["provenance"]["task"]["id"].startswith("image.generate.")
    assert state["provenance"]["workflows"]
    assert "private-prompt" not in json.dumps(state)
    queue = client.get("/api/v1/queue").json()
    assert queue["runtime"] == {"queue_running": [], "queue_pending": []}


def test_action_examples_are_typed_artifacts_not_media_urls(tmp_path):
    client = ready_client(tmp_path)
    image_action = client.get("/api/v1/actions/image.generate").json()
    animation_action = client.get("/api/v1/actions/animation.generate").json()

    image_options = next(item for item in image_action["controls"] if item["id"] == "category")
    motion_options = next(item for item in animation_action["controls"] if item["id"] == "action")
    assert all(option["example"]["kind"] == "Image" for option in image_options["options"])
    assert all(
        option["example"]["url"].startswith("/api/v1/artifacts/")
        for option in image_options["options"]
    )
    assert all(option["example"]["kind"] == "FrameSeq" for option in motion_options["options"])
    sequence_id = motion_options["options"][0]["example"]["id"]
    sequence = client.get(f"/api/v1/artifacts/{sequence_id}/sequence")
    assert sequence.status_code == 200
    assert len(sequence.json()["frames"]) == 4
    assert all(not item["meta"].get("system") for item in client.get("/api/v1/artifacts").json())


def test_prompt_tool_is_model_neutral_and_deterministic():
    compiler = SpritePromptCompiler()
    request = ImagePromptRequest(
        caption="a soup knight",
        category="character",
        style="pixel",
        mode="t2i",
        camera_preset="top45",
        orientation="right",
    )
    first = compiler.compile_image(request)
    second = compiler.compile_image(request)
    assert first.to_dict() == second.to_dict()
    assert first.task == "image"
    assert first.mode == "t2i"
    assert first.metadata["compiler_version"] == "sprite_prompt_package_v1"
    assert first.camera_contract.pitch_deg == 25
    assert "clip" not in first.prompt.lower()
    assert "2D game illustration" not in first.prompt
    assert "pure green-screen background (#00FF00)" in first.prompt
    video = compiler.compile_video(VideoPromptRequest(caption="soup knight", action="walk"))
    assert video.task == "video"
    assert video.metadata["action"] == "walk"
    assert len(compiler.image_matrix("soup knight")) == 24
    assert len(compiler.video_actions("soup knight")) == 9


def test_prompt_node_preserves_old_inputs_and_returns_three_generic_text_ports():
    node = CS_CompilePromptPacket()
    prompt, negative, metadata = node.compile(
        "image.generate", "a soup knight", "character", "pixel", "idle", "level", "s"
    )
    assert prompt.startswith("Create one complete asset")
    assert "extra characters" in negative
    assert "pure green-screen background (#00FF00)" in prompt
    assert "2D game illustration" not in prompt
    assert '"compiler_version": "sprite_prompt_package_v1"' in metadata
    assert CS_CompilePromptPacket.RETURN_TYPES == ("STRING", "STRING", "STRING")
    assert CS_CompilePromptPacket.RETURN_NAMES == ("prompt", "negative_prompt", "metadata")


def test_runtime_defaults_select_the_configured_recipe_when_model_is_omitted(tmp_path):
    client = ready_client(tmp_path)
    defaults = client.get("/api/v1/runtimes/rt_test/defaults")
    assert defaults.status_code == 200
    binding = defaults.json()["defaults"]["image.generate"]
    assert binding["workflow_id"].startswith("core-image-")
    updated = client.put(
        "/api/v1/runtimes/rt_test/defaults/image.generate",
        json={
            "workflow_id": binding["workflow_id"],
            "model_id": "test-model.safetensors",
        },
    )
    assert updated.status_code == 200
    project = client.post("/api/v1/projects", json={"type": "static"}).json()
    run = client.post(
        "/api/v1/actions/image.generate/runs",
        json={
            "project": project["id"],
            "inputs": {},
            "values": {"prompt": "a soup knight", "count": 1},
        },
    )
    assert run.status_code == 202
    assert wait(client, run.json()["id"])["provenance"]["recipe"] == binding["workflow_id"]


def test_animation_run_returns_one_typed_frame_sequence(tmp_path):
    client = ready_client(tmp_path)
    project = client.post("/api/v1/projects", json={"type": "character"}).json()
    source = client.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "Image", "media_type": "image/png"},
        content=PNG,
    ).json()
    response = client.post(
        "/api/v1/actions/animation.generate/runs",
        json={
            "project": project["id"],
            "inputs": {"character": source["id"]},
            "values": {
                "action": "walk",
                "view": "level",
                "direction": "s",
                "count": 8,
                "seed": 240815,
            },
        },
    )
    assert response.status_code == 202
    state = wait(client, response.json()["id"])
    assert state["status"] == "succeeded"
    assert len(state["artifacts"]) == 1
    sequence_ref = state["artifacts"][0]
    assert sequence_ref["kind"] == "FrameSeq"
    sequence = client.get(f"/api/v1/artifacts/{sequence_ref['id']}/sequence").json()
    assert sequence["sequence"] == {
        "schema": "cooksprite.frame-sequence/v1",
        "action": "walk",
        "view": "level",
        "direction": "s",
        "frames": [item["id"] for item in sequence["frames"]],
    }
    assert len(sequence["frames"]) == 8
    assert all(item["kind"] == "Image" for item in sequence["frames"])


def test_frame_sequence_normal_expands_and_preserves_pairing(tmp_path):
    client = ready_client(tmp_path)
    project = client.post("/api/v1/projects", json={"type": "character"}).json()
    frame_ids = []
    for index in range(3):
        frame_ids.append(
            client.post(
                "/api/v1/artifacts",
                params={"project_id": project["id"], "kind": "Image", "media_type": "image/png"},
                content=PNG + bytes([index]),
            ).json()["id"]
        )
    manifest = json.dumps(
        {
            "schema": "cooksprite.frame-sequence/v1",
            "action": "walk",
            "view": "level",
            "direction": "s",
            "frames": frame_ids,
        }
    ).encode()
    sequence = client.post(
        "/api/v1/artifacts",
        params={
            "project_id": project["id"],
            "kind": "FrameSeq",
            "media_type": "application/vnd.cooksprite.frame-sequence+json",
        },
        content=manifest,
    ).json()
    run = client.post(
        "/api/v1/actions/normal.generate/runs",
        json={"project": project["id"], "inputs": {"source": sequence["id"]}, "values": {}},
    )
    assert run.status_code == 202
    state = wait(client, run.json()["id"])
    assert state["status"] == "succeeded"
    assert len(state["artifacts"]) == 3
    assert [item["meta"]["source_artifacts"][0] for item in state["artifacts"]] == frame_ids
    graph = ProtocolComfy.submitted[-1]
    sinks = [item for item in graph.values() if item["class_type"] == "CS_StoreArtifact"]
    assert [
        urllib.parse.parse_qs(urllib.parse.urlparse(item["inputs"]["upload_url"]).query)[
            "source_artifact"
        ][0]
        for item in sinks
    ] == frame_ids


def test_schema_v2_image_sequences_migrate_to_typed_manifest(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Legacy walk", "character")
    run_id = "run_legacy_walk"
    store.create_run(
        run_id,
        "rt_legacy",
        action_id="animation.generate",
        project_id=project.id,
        request={
            "values": {
                "action": "walk",
                "view": ["level"],
                "directions": ["s"],
            }
        },
    )
    frame_ids = []
    for index in range(2):
        frame = store.put_artifact(
            PNG + bytes([index]),
            "image/png",
            "FrameSeq",
            {"run_id": run_id},
            project_id=project.id,
        )
        frame_ids.append(frame.id)
        store.attach_run_artifact(run_id, frame.id)
    store.db.execute("PRAGMA user_version=2")
    store.db.commit()
    store.db.close()

    migrated = Store(tmp_path)
    assert migrated.db.execute("PRAGMA user_version").fetchone()[0] == 6
    assert [migrated.artifact(item)["kind"] for item in frame_ids] == ["Image", "Image"]
    run = migrated.run(run_id)
    sequence_ids = json.loads(run["artifacts"])
    assert len(sequence_ids) == 1
    sequence = migrated.artifact(sequence_ids[0])
    assert sequence["kind"] == "FrameSeq"
    assert sequence["media_type"] == "application/vnd.cooksprite.frame-sequence+json"
    assert json.loads(migrated.artifact_bytes(sequence["id"])) == {
        "schema": "cooksprite.frame-sequence/v1",
        "action": "walk",
        "view": "level",
        "direction": "s",
        "frames": frame_ids,
    }


def test_document_requires_etag_and_detects_conflict(tmp_path):
    client = TestClient(create_app(tmp_path, ProtocolComfy, allow_test_runtime=True))
    project = client.post("/api/v1/projects", json={"type": "character"}).json()
    current = client.get(f"/api/v1/projects/{project['id']}/document").json()
    document = current["document"]
    document["character"]["pivot"] = {"x": 0.42, "y": 0.93}
    missing = client.put(f"/api/v1/projects/{project['id']}/document", json=document)
    assert missing.status_code == 409
    saved = client.put(
        f"/api/v1/projects/{project['id']}/document",
        json=document,
        headers={"If-Match": current["etag"]},
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    conflict = client.put(
        f"/api/v1/projects/{project['id']}/document",
        json=document,
        headers={"If-Match": current["etag"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "document_conflict"


def test_curated_document_track_materializes_as_reusable_frame_sequence(tmp_path):
    client = TestClient(create_app(tmp_path, ProtocolComfy, allow_test_runtime=True))
    project = client.post("/api/v1/projects", json={"type": "character"}).json()
    frame_ids = []
    for index in range(3):
        frame = client.post(
            "/api/v1/artifacts",
            params={
                "project_id": project["id"],
                "kind": "Image",
                "media_type": "image/png",
            },
            content=PNG + bytes([index]),
        ).json()
        frame_ids.append(frame["id"])
    current = client.get(f"/api/v1/projects/{project['id']}/document").json()
    current["document"]["character"]["clips"] = [
        {
            "id": "clip_walk",
            "name": "walk",
            "action": "walk",
            "loop": "linear",
            "views": [
                {
                    "id": "level",
                    "enabled": True,
                    "tracks": [
                        {
                            "direction": "s",
                            "frames": [
                                {
                                    "id": f"frame_{index}",
                                    "artifact": artifact_id,
                                    "duration_ms": 100,
                                    "offset_x": 0,
                                    "offset_y": 0,
                                }
                                for index, artifact_id in enumerate(frame_ids)
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    saved = client.put(
        f"/api/v1/projects/{project['id']}/document",
        json=current["document"],
        headers={"If-Match": current["etag"]},
    )
    assert saved.status_code == 200
    result = client.post(
        f"/api/v1/projects/{project['id']}/sequences",
        json={"action": "walk", "view": "level", "direction": "s"},
    )
    assert result.status_code == 201
    body = result.json()
    assert body["artifact"]["kind"] == "FrameSeq"
    assert body["artifact"]["meta"]["role"] == "curated_sequence"
    assert body["sequence"] == {
        "schema": "cooksprite.frame-sequence/v1",
        "action": "walk",
        "view": "level",
        "direction": "s",
        "frames": frame_ids,
    }
    assert [item["id"] for item in body["frames"]] == frame_ids


def test_materializing_an_empty_track_returns_explicit_error(tmp_path):
    client = TestClient(create_app(tmp_path, ProtocolComfy, allow_test_runtime=True))
    project = client.post("/api/v1/projects", json={"type": "character"}).json()
    result = client.post(
        f"/api/v1/projects/{project['id']}/sequences",
        json={"action": "walk", "view": "level", "direction": "s"},
    )
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == "track_empty"


def test_artifact_favorite_is_real_persisted_state(tmp_path):
    client = TestClient(create_app(tmp_path, ProtocolComfy, allow_test_runtime=True))
    artifact = client.post(
        "/api/v1/artifacts",
        params={"kind": "Image", "media_type": "image/png", "title": "Chef"},
        content=PNG,
    ).json()
    changed = client.patch(f"/api/v1/artifacts/{artifact['id']}", json={"favorite": True})
    assert changed.status_code == 200
    assert changed.json()["favorite"] is True
    assert client.get(f"/api/v1/artifacts/{artifact['id']}").json()["favorite"] is True


def test_runtime_artifact_records_its_source_lineage(tmp_path):
    app = create_app(tmp_path, ProtocolComfy, allow_test_runtime=True)
    client = TestClient(app)
    project = client.post("/api/v1/projects", json={"type": "static"}).json()
    source = client.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "Image", "media_type": "image/png"},
        content=PNG,
    ).json()
    app.state.store.create_run(
        "run_lineage",
        None,
        action_id="normal.generate",
        project_id=project["id"],
        request={"project": project["id"], "inputs": {"source": source["id"]}, "values": {}},
    )
    normal = client.post(
        "/api/v1/internal/artifacts",
        params={"run_id": "run_lineage", "kind": "NormalMap", "media_type": "image/png"},
        content=PNG + b"normal-lineage",
    )
    assert normal.status_code == 200
    assert normal.json()["meta"]["source_artifacts"] == [source["id"]]


def test_static_package_is_unique_format_and_marks_integrity(tmp_path):
    client = ready_client(tmp_path)
    project = client.post("/api/v1/projects", json={"name": "Chef", "type": "static"}).json()
    diffuse = client.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "Image", "media_type": "image/png"},
        content=PNG,
    ).json()
    normal_png = PNG + b"normal"
    normal = client.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "NormalMap", "media_type": "image/png"},
        content=normal_png,
    ).json()
    current = client.get(f"/api/v1/projects/{project['id']}/document").json()
    current["document"]["static"].update({"primary": diffuse["id"], "normal": normal["id"]})
    assert (
        client.put(
            f"/api/v1/projects/{project['id']}/document",
            json=current["document"],
            headers={"If-Match": current["etag"]},
        ).status_code
        == 200
    )
    run = client.post(f"/api/v1/projects/{project['id']}/exports", json={}).json()
    state = wait(client, run["id"])
    assert state["status"] == "succeeded"
    package = state["artifacts"][0]
    assert package["kind"] == "CookSpritePack"
    with zipfile.ZipFile(io.BytesIO(client.get(package["url"]).content)) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "provenance.json",
            "frames/primary.png",
            "normals/primary.png",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema"] == "cooksprite.package/v1"
        assert manifest["integrity_warnings"] == []


def test_openapi_contains_the_same_public_surface(tmp_path):
    client = TestClient(create_app(tmp_path, ProtocolComfy, allow_test_runtime=True))
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    for path in [
        "/api/v1/actions",
        "/api/v1/actions/{action_id}",
        "/api/v1/actions/{action_id}/runs",
        "/api/v1/projects/{project_id}/document",
        "/api/v1/artifacts/{artifact_id}/trash",
        "/api/v1/artifacts/{artifact_id}/sequence",
        "/api/v1/queue",
    ]:
        assert path in paths
