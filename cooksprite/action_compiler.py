"""Compile stable Actions into real, private ComfyUI graphs."""

from __future__ import annotations

import copy
import secrets
from typing import Any

from .bridge import ArtifactBridge
from .domain import ActionDescriptor
from .execution import ExecutionPlan, PlanBuilder
from .recipes import Recipe


class ActionCompileError(ValueError):
    pass


CATEGORY_PROMPTS = {
    "character": "a single full-body game character, centered, complete silhouette",
    "weapon": "a single game weapon, centered, fully visible",
    "prop": "a single game prop, centered, fully visible",
    "terrain": "a seamless game terrain tile, orthographic",
    "scene": "a single isolated game environment element",
    "vfx": "a single game visual effect, centered",
}
ACTION_PROMPTS = {
    "idle": "idle breathing animation keyframes",
    "walk": "walk cycle animation keyframes",
    "run": "run cycle animation keyframes",
    "attack": "attack animation keyframes",
    "cast": "spell casting animation keyframes",
    "hit": "hit reaction animation keyframes",
    "jump": "jump animation keyframes",
    "death": "fall down animation keyframes",
}
DIRECTION_PROMPTS = {
    "n": "facing north, rear view",
    "ne": "facing north-east",
    "e": "facing east, side view",
    "se": "facing south-east",
    "s": "facing south, front view",
    "sw": "facing south-west",
    "w": "facing west, side view",
    "nw": "facing north-west",
}


def _prompt_packet(action_id: str, values: dict[str, Any]) -> tuple[str, str]:
    user = str(values.get("prompt") or "").strip()
    negative = (
        "cropped, cut off, multiple views, contact sheet, text, watermark, logo, "
        "photographic background, gradient background, shadow on background"
    )
    if action_id == "animation.generate":
        parts = [
            ACTION_PROMPTS.get(str(values.get("action")), "animation keyframes"),
            "orthographic game sprite",
            "45 degree top-down view" if values.get("view") == "top45" else "level view",
            DIRECTION_PROMPTS.get(str(values.get("direction")), "front view"),
            "preserve the same character identity, outfit, proportions and colors",
            "one pose per image",
            "flat pure chroma green background, RGB 0 255 0",
        ]
    else:
        parts = [
            CATEGORY_PROMPTS.get(str(values.get("category")), "a single game asset"),
            "pixel art, crisp hard pixel edges, limited palette, no antialiasing"
            if values.get("style") == "pixel"
            else "clean game concept art",
            "flat pure chroma green background, RGB 0 255 0",
        ]
    if user:
        parts.insert(0, user)
    return ", ".join(parts), negative


class ActionCompiler(PlanBuilder):
    """Lower one Action through a verified Recipe; no demo/fallback route exists."""

    def __init__(self, bridge: ArtifactBridge):
        super().__init__(bridge, None, node_prefix="cs_bridge")

    def _add(self, class_type: str, inputs: dict[str, Any]) -> str:
        return self.add(class_type, inputs)

    def _load(self, artifact_id: str, video: bool = False) -> str:
        return str(self.load_artifact(artifact_id, video=video)[0])

    def _store(
        self,
        value: list[Any],
        kind: str,
        source_artifact: str = "",
    ) -> str:
        return self.store_artifact(value, kind, source_artifact=source_artifact)

    def compile(
        self,
        action: ActionDescriptor,
        inputs: dict[str, list[str]],
        values: dict[str, Any],
        run_id: str,
        recipe: Recipe,
    ) -> ExecutionPlan:
        self.run_id = run_id
        if action.id == "sprite.export":
            raise ActionCompileError("sprite.export is packaged by CookSprite, not ComfyUI")
        if recipe.workflow:
            self._compile_imported(action, inputs, values, recipe)
        elif recipe.family == "comfy.core-checkpoint":
            self._compile_core_image(action, inputs, values, recipe)
        elif action.id == "normal.generate":
            self._compile_normals(inputs, values)
        elif action.id == "sheet.slice":
            self._compile_sheet(inputs, values)
        elif action.id == "video.sample":
            self._compile_video(inputs, values)
        else:
            raise ActionCompileError(f"recipe {recipe.id} cannot compile Action {action.id}")
        return self.build()

    def _compile_core_image(
        self,
        action: ActionDescriptor,
        inputs: dict[str, list[str]],
        values: dict[str, Any],
        recipe: Recipe,
    ) -> None:
        if not recipe.checkpoint:
            raise ActionCompileError("checkpoint recipe has no checkpoint")
        loader = self._add("CheckpointLoaderSimple", {"ckpt_name": recipe.checkpoint})
        positive_text, negative_text = _prompt_packet(action.id, values)
        positive = self._add("CLIPTextEncode", {"text": positive_text, "clip": [loader, 1]})
        negative = self._add("CLIPTextEncode", {"text": negative_text, "clip": [loader, 1]})
        count = max(1, min(int(values.get("count", 1)), 16))
        source_ids = (
            inputs.get("reference") or inputs.get("source") or inputs.get("character") or []
        )
        source_id = source_ids[0] if source_ids else ""
        if source_id:
            loaded = self._load(source_id)
            scaled = self._add(
                "ImageScale",
                {
                    "image": [loaded, 0],
                    "upscale_method": "nearest-exact",
                    "width": 512,
                    "height": 512,
                    "crop": "disabled",
                },
            )
            encoded = self._add("VAEEncode", {"pixels": [scaled, 0], "vae": [loader, 2]})
            latent = [encoded, 0]
            if count > 1:
                repeated = self._add("RepeatLatentBatch", {"samples": latent, "amount": count})
                latent = [repeated, 0]
            denoise = max(0.01, min(float(values.get("strength", 0.65)), 1.0))
        else:
            empty = self._add(
                "EmptyLatentImage", {"width": 512, "height": 512, "batch_size": count}
            )
            latent = [empty, 0]
            denoise = 1.0
        seed = int(values.get("seed", -1))
        if seed < 0:
            seed = secrets.randbelow(2**63 - 1)
        sampler = self._add(
            "KSampler",
            {
                "model": [loader, 0],
                "seed": seed,
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "positive": [positive, 0],
                "negative": [negative, 0],
                "latent_image": latent,
                "denoise": denoise,
            },
        )
        decoded = self._add("VAEDecode", {"samples": [sampler, 0], "vae": [loader, 2]})
        isolated = self._add("CS_IsolateOnGreen", {"image": [decoded, 0], "tolerance": 0.22})
        output: list[Any] = [isolated, 0]
        if values.get("style") == "pixel":
            pixel = self._add(
                "CS_Pixelize", {"image": output, "target_width": 128, "target_height": 128}
            )
            output = [pixel, 0]
        self._store(output, "Image")

    def _compile_normals(self, inputs: dict[str, list[str]], values: dict[str, Any]) -> None:
        for source_id in inputs.get("source", []):
            loaded = self._load(source_id)
            normal = self._add(
                "CS_NormalEstimate",
                {
                    "image": [loaded, 0],
                    "strength": float(values.get("strength", 1)),
                    "flip_y": bool(values.get("flip_y", False)),
                },
            )
            self._store([normal, 0], "NormalMap", source_id)

    def _compile_sheet(self, inputs: dict[str, list[str]], values: dict[str, Any]) -> None:
        source_id = inputs["sheet"][0]
        loaded = self._load(source_id)
        sliced = self._add(
            "CS_SliceSpriteSheet",
            {
                "image": [loaded, 0],
                "columns": int(values.get("columns", 0)),
                "rows": int(values.get("rows", 0)),
                "frame_width": int(values.get("frame_width", 64)),
                "frame_height": int(values.get("frame_height", 64)),
                "margin": int(values.get("margin", 0)),
                "spacing": int(values.get("spacing", 0)),
                "exclude_empty": bool(values.get("exclude_empty", True)),
            },
        )
        self._store([sliced, 0], "Image", source_id)

    def _compile_video(self, inputs: dict[str, list[str]], values: dict[str, Any]) -> None:
        source_id = inputs["video"][0]
        loaded = self._load(source_id, video=True)
        # CS_LoadVideoArtifact performs sampling because ComfyUI has no one
        # universal VIDEO tensor contract across core and third-party nodes.
        self.graph[loaded]["inputs"].update(
            {
                "sample_fps": float(values.get("sample_fps", 12)),
                "max_frames": int(values.get("max_frames", 48)),
            }
        )
        self._store([loaded, 0], "Image", source_id)

    def _compile_imported(
        self,
        action: ActionDescriptor,
        inputs: dict[str, list[str]],
        values: dict[str, Any],
        recipe: Recipe,
    ) -> None:
        graph = copy.deepcopy(recipe.workflow or {})
        if not isinstance(graph, dict):
            raise ActionCompileError("imported recipe workflow must be API-format JSON")

        def set_slot(name: str, value: Any) -> None:
            address = recipe.slots.get(name)
            if not address:
                return
            node_id, separator, input_name = address.partition(".")
            if not separator or node_id not in graph:
                raise ActionCompileError(f"invalid recipe slot {name}: {address}")
            graph[node_id].setdefault("inputs", {})[input_name] = value

        positive, negative = _prompt_packet(action.id, values)
        set_slot("text", positive)
        set_slot("negative", negative)
        set_slot("model", recipe.checkpoint)
        seed = int(values.get("seed", -1))
        set_slot("seed", secrets.randbelow(2**63 - 1) if seed < 0 else seed)
        set_slot("count", int(values.get("count", 1)))
        source_ids = (
            inputs.get("reference") or inputs.get("source") or inputs.get("character") or []
        )
        if source_ids:
            loaded = self._load(source_ids[0])
            set_slot("image", [loaded, 0])
        for key, node in graph.items():
            if key in self.graph:
                raise ActionCompileError(f"imported workflow node collides with bridge node: {key}")
            self.graph[str(key)] = node
        if not recipe.output or len(recipe.output) != 2:
            raise ActionCompileError("imported recipe needs one typed output [node, index]")
        output: list[Any] = recipe.output
        if action.id == "image.generate" and values.get("style") == "pixel":
            pixel = self._add(
                "CS_Pixelize", {"image": output, "target_width": 128, "target_height": 128}
            )
            output = [pixel, 0]
        self._store(output, "Image")
