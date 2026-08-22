"""CookSprite's headless ComfyUI bridge and media nodes.

The package has no ComfyUI browser extension.  Every node is safe to execute in
API mode and all inference/model lifecycle remains owned by ComfyUI.
"""

from __future__ import annotations

import io
import json
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .normalcrafter import CS_NormalCrafterBatch, CS_NormalCrafterSequence

try:  # Prompt Tool tests and API tooling do not need the compute-only torch dependency.
    import torch
except ImportError:  # pragma: no cover - ComfyUI always supplies torch at node runtime.
    torch = None


RUNTIME_INFO_NAME = "RUNTIME.json"
_RUNTIME_INFO_ROUTE_REGISTERED = False


def runtime_info_payload(path: str | Path | None = None) -> dict | None:
    """Read the deployment identity placed beside the installed node package.

    The installer writes this file before its atomic directory swap.  The
    source checkout intentionally has no such file, so importing nodes for
    tests or tooling never invents a runtime identity.
    """

    target = Path(path) if path is not None else Path(__file__).with_name(RUNTIME_INFO_NAME)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _register_runtime_info_route() -> None:
    """Add one read-only identity endpoint when imported by a ComfyUI server."""

    global _RUNTIME_INFO_ROUTE_REGISTERED
    if _RUNTIME_INFO_ROUTE_REGISTERED:
        return
    try:
        from aiohttp import web
        from server import PromptServer

        routes = PromptServer.instance.routes
    except (ImportError, AttributeError):
        # Unit tests and API-only tooling deliberately do not depend on
        # ComfyUI/aiohttp.  A managed worker rejects the missing endpoint at
        # doctor/start time instead of silently accepting it.
        return

    @routes.get("/cooksprite/runtime-info")
    async def cooksprite_runtime_info(_request):
        payload = runtime_info_payload()
        if payload is None:
            return web.json_response(
                {"error": "CookSprite runtime identity is unavailable"}, status=503
            )
        return web.json_response(payload, headers={"Cache-Control": "no-store"})

    _RUNTIME_INFO_ROUTE_REGISTERED = True


def _tensor(image: Image.Image):
    if torch is None:
        raise RuntimeError("CookSprite media nodes require ComfyUI's torch runtime")
    pixels = np.array(image, dtype=np.float32, copy=True) / 255.0
    return torch.from_numpy(pixels).unsqueeze(0)


def _array(value) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    return np.asarray(value)


def _png(value, kind, mask=None):
    array = np.clip(_array(value), 0.0, 1.0)
    has_alpha = array.shape[-1] >= 4
    if kind == "NormalMap":
        rgb = np.rint(array[..., :3] * 255.0).astype("uint8")
    else:
        rgb = (array[..., :3] * 255.0).astype("uint8")
    if mask is not None:
        alpha = np.clip(_array(mask), 0.0, 1.0)
        if alpha.ndim == 3 and alpha.shape[-1] == 1:
            alpha = alpha[..., 0]
        if alpha.ndim != 2:
            raise ValueError("MASK frame must have shape [height,width]")
        rgba = np.concatenate((rgb, np.rint(alpha * 255.0).astype("uint8")[..., None]), axis=-1)
        if kind != "NormalMap":
            rgba[rgba[..., 3] == 0, :3] = 0
        image = Image.fromarray(rgba, "RGBA")
    elif has_alpha:
        rgba = (array[..., :4] * 255.0).astype("uint8")
        if kind != "NormalMap":
            rgba[rgba[..., 3] == 0, :3] = 0
        image = Image.fromarray(rgba, "RGBA")
    else:
        image = Image.fromarray(rgb, "RGB")
    if kind != "NormalMap" and mask is None and not has_alpha:
        rgba = image.convert("RGBA")
        pixels = np.array(rgba)
        green = (pixels[:, :, 1] > 245) & (pixels[:, :, 0] < 16) & (pixels[:, :, 2] < 16)
        pixels[green, 3] = 0
        image = Image.fromarray(pixels, "RGBA")
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _read(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "CookSprite-Comfy/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(), response.headers.get_content_type()


def _post(url: str, data: bytes, content_type: str = "image/png"):
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": content_type, "User-Agent": "CookSprite-Comfy/1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _append_query(url: str, **values) -> str:
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return url + separator + urllib.parse.urlencode(values)


@dataclass
class _BridgeFrameSequence:
    """A re-playable, lazy FrameSeq reader carried inside the node graph."""

    frames: list[dict]
    temporal: dict | None = None
    canvas: tuple[int, int] | None = None
    _first: np.ndarray | None = field(default=None, init=False, repr=False)

    @staticmethod
    def _rgba(frame: dict) -> np.ndarray:
        data, _ = _read(str(frame["url"]))
        return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"), dtype=np.uint8).copy()

    def resolve_canvas(self) -> tuple[int, int]:
        if self.canvas is not None:
            return self.canvas
        if not self.frames:
            raise ValueError("FrameSeq bridge manifest has no frames")
        self._first = self._rgba(self.frames[0])
        self.canvas = (int(self._first.shape[1]), int(self._first.shape[0]))
        return self.canvas

    def iter_rgba(self):
        expected = self.resolve_canvas()
        for index, frame in enumerate(self.frames):
            value = self._first if index == 0 and self._first is not None else self._rgba(frame)
            assert value is not None
            canvas = (int(value.shape[1]), int(value.shape[0]))
            if canvas != expected:
                raise ValueError("all long FrameSeq frames must use the same canvas")
            yield value


class CS_LoadArtifact:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"artifact_url": ("STRING", {"multiline": False})}}

    # The first image/mask outputs deliberately keep their historic indexes.
    # Long FrameSeq and PixelGeometryPlan are private bridge values, not raw
    # filesystem paths crossing a Tool boundary.
    RETURN_TYPES = ("IMAGE", "MASK", "CS_FRAMESEQ", "CS_PIXEL_PLAN")
    RETURN_NAMES = ("image", "mask", "frames", "pixel_plan")
    FUNCTION = "load"
    CATEGORY = "CookSprite/Bridge"

    @staticmethod
    def _decode(data: bytes) -> tuple[object, object]:
        image = Image.open(io.BytesIO(data))
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            alpha = np.asarray(rgba, dtype=np.uint8)[..., 3].astype(np.float32) / 255.0
            image = rgba.convert("RGB")
        else:
            image = image.convert("RGB")
            alpha = np.ones((image.height, image.width), dtype=np.float32)
        return _tensor(image), torch.from_numpy(alpha).unsqueeze(0)

    def load(self, artifact_url):
        data, media_type = _read(artifact_url)
        if media_type == "application/vnd.cooksprite.pixel-geometry-plan+json":
            from .pixel.plan import PixelGeometryPlanValue, validate_payload

            return (None, None, None, PixelGeometryPlanValue(validate_payload(json.loads(data))))
        if media_type == "application/vnd.cooksprite.bridge-frame-sequence+json":
            manifest = json.loads(data.decode("utf-8"))
            if manifest.get("schema") != "cooksprite.bridge-frame-sequence/v1":
                raise ValueError("unsupported CookSprite FrameSeq bridge manifest")
            frames = manifest.get("frames") or []
            if not 1 <= len(frames) <= 240:
                raise ValueError("CookSprite FrameSeq must contain 1 to 240 frames")
            raw_canvas = manifest.get("canvas")
            canvas = (
                (int(raw_canvas[0]), int(raw_canvas[1]))
                if isinstance(raw_canvas, list) and len(raw_canvas) == 2
                else None
            )
            return (
                None,
                None,
                _BridgeFrameSequence(
                    [dict(frame) for frame in frames],
                    dict(manifest["temporal"]) if isinstance(manifest.get("temporal"), dict) else None,
                    canvas,
                ),
                None,
            )
        if media_type != "application/vnd.cooksprite.bridge-image-batch+json":
            image, mask = self._decode(data)
            return (image, mask, None, None)
        manifest = json.loads(data.decode("utf-8"))
        if manifest.get("schema") != "cooksprite.bridge-image-batch/v1":
            raise ValueError("unsupported CookSprite image batch manifest")
        frames = manifest.get("frames") or []
        if not 1 <= len(frames) <= 32:
            raise ValueError("CookSprite image batch must contain 1 to 32 frames")
        images = []
        masks = []
        canvas = None
        for frame in frames:
            frame_data, _ = _read(str(frame["url"]))
            image, mask = self._decode(frame_data)
            size = tuple(image.shape[1:3])
            if canvas is None:
                canvas = size
            elif size != canvas:
                raise ValueError("all Sprite chunk frames must use the same canvas")
            images.append(image)
            masks.append(mask)
        return (torch.cat(images, dim=0), torch.cat(masks, dim=0), None, None)


class CS_StoreArtifact:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "upload_url": ("STRING", {"multiline": False}),
            },
            "optional": {
                "value": ("IMAGE",),
                "mask": ("MASK",),
                "sequence": ("CS_PIXEL_SEQUENCE",),
                "normal_sequence": ("CS_NORMAL_SEQUENCE",),
                "pixel_plan": ("CS_PIXEL_PLAN",),
            },
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "store"
    CATEGORY = "CookSprite/Bridge"
    OUTPUT_NODE = True

    def store(
        self,
        upload_url,
        value=None,
        mask=None,
        sequence=None,
        normal_sequence=None,
        pixel_plan=None,
    ):
        refs = []
        kind = urllib.parse.parse_qs(urllib.parse.urlparse(upload_url).query).get(
            "kind", ["Image"]
        )[0]
        if pixel_plan is not None:
            payload = getattr(pixel_plan, "payload", pixel_plan)
            if not isinstance(payload, dict):
                raise ValueError("CS_StoreArtifact received an invalid PixelGeometryPlan")
            body = _post(
                upload_url,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
                "application/json",
            )
            return (json.dumps([json.loads(body.decode())["id"]]),)
        if sequence is not None:
            if not hasattr(sequence, "iter_frames"):
                raise ValueError("CS_StoreArtifact received an invalid streamed pixel sequence")
            try:
                for index, frame in enumerate(sequence.iter_frames()):
                    rgba = np.asarray(frame, dtype=np.uint8)
                    if rgba.ndim != 3 or rgba.shape[-1] != 4:
                        raise ValueError("streamed pixel frame must be RGBA")
                    body = _post(
                        _append_query(
                            upload_url,
                            output_index=index,
                            canvas_width=int(rgba.shape[1]),
                            canvas_height=int(rgba.shape[0]),
                        ),
                        _png(rgba[..., :3].astype(np.float32) / 255.0, kind, rgba[..., 3].astype(np.float32) / 255.0),
                    )
                    refs.append(json.loads(body.decode())["id"])
            finally:
                close = getattr(sequence, "close", None)
                if callable(close):
                    close()
            return (json.dumps(refs),)
        if normal_sequence is not None:
            if not hasattr(normal_sequence, "iter_normal_frames"):
                raise ValueError("CS_StoreArtifact received an invalid streamed normal sequence")
            try:
                for index, (normal, alpha) in enumerate(normal_sequence.iter_normal_frames()):
                    normal_value = np.asarray(normal, dtype=np.float32)
                    alpha_value = np.asarray(alpha, dtype=np.float32)
                    if normal_value.ndim != 3 or normal_value.shape[-1] != 3:
                        raise ValueError("streamed normal frame must have three channels")
                    if alpha_value.shape != normal_value.shape[:2]:
                        raise ValueError("streamed normal alpha must match its normal frame")
                    body = _post(
                        _append_query(
                            upload_url,
                            output_index=index,
                            canvas_width=int(normal_value.shape[1]),
                            canvas_height=int(normal_value.shape[0]),
                        ),
                        _png(normal_value, kind, alpha_value),
                    )
                    refs.append(json.loads(body.decode())["id"])
            finally:
                close = getattr(normal_sequence, "close", None)
                if callable(close):
                    close()
            return (json.dumps(refs),)
        if value is None:
            raise ValueError(
                "CS_StoreArtifact needs image, sequence, normal sequence, or PixelGeometryPlan input"
            )
        mask_array = _array(mask) if mask is not None else None
        for index, frame in enumerate(value):
            frame_mask = None
            if mask_array is not None:
                frame_mask = mask_array[index if mask_array.ndim == 3 else 0]
            body = _post(
                _append_query(
                    upload_url,
                    output_index=index,
                    canvas_width=int(_array(frame).shape[1]),
                    canvas_height=int(_array(frame).shape[0]),
                ),
                _png(frame, kind, frame_mask),
            )
            refs.append(json.loads(body.decode())["id"])
        return (json.dumps(refs),)


class CS_Pixelize:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "target_width": ("INT", {"default": 128, "min": 16, "max": 512}),
                "target_height": ("INT", {"default": 128, "min": 16, "max": 512}),
                "profile": ("STRING", {"default": "production", "enum": ["production", "fidelity", "balanced", "graphic"]}),
                "palette_budget": ("INT", {"default": 0, "min": 0, "max": 256}),
                "padding_x": ("INT", {"default": -1, "min": -1, "max": 256}),
                "padding_y": ("INT", {"default": -1, "min": -1, "max": 256}),
                "variants": ("BOOLEAN", {"default": False}),
                "enabled": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "mask": ("MASK",),
                # New Action graphs use one longest-edge size.  The legacy
                # width/height ports remain for saved ComfyUI workflows.
                "target_size": ("INT", {"default": 0, "min": 0, "max": 512}),
                "outline": ("BOOLEAN", {"default": True}),
                "outline_color": ("STRING", {"default": "#000000"}),
                "sequence_mode": ("STRING", {"default": "auto", "enum": ["auto", "independent", "chunk", "continuous"]}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Pixel"

    def run(
        self,
        image,
        target_width,
        target_height,
        profile="production",
        palette_budget=0,
        padding_x=-1,
        padding_y=-1,
        variants=False,
        enabled=True,
        mask=None,
        target_size=0,
        outline=True,
        outline_color="#000000",
        sequence_mode="auto",
    ):
        if not enabled:
            passthrough_mask = mask
            if passthrough_mask is None:
                passthrough_mask = torch.ones(image.shape[0], image.shape[1], image.shape[2], device=image.device, dtype=image.dtype)
            return (image, passthrough_mask)
        from .pixel.adapter import pixelize_batch

        output, output_mask = pixelize_batch(
            image.detach().cpu().numpy(),
            mask.detach().cpu().numpy() if mask is not None else None,
            int(target_width),
            int(target_height),
            profile=str(profile),
            palette_budget=int(palette_budget),
            padding_x=int(padding_x),
            padding_y=int(padding_y),
            variants=bool(variants),
            target_size=int(target_size) if int(target_size) > 0 else None,
            outline=bool(outline),
            outline_color=str(outline_color),
            sequence_mode=str(sequence_mode),
        )
        return (
            torch.from_numpy(output).to(dtype=image.dtype),
            torch.from_numpy(output_mask).to(dtype=image.dtype),
        )


class CS_PixelizePair:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "normal": ("IMAGE",),
                "target_width": ("INT", {"default": 128, "min": 16, "max": 512}),
                "target_height": ("INT", {"default": 128, "min": 16, "max": 512}),
                "profile": ("STRING", {"default": "production", "enum": ["production", "fidelity", "balanced", "graphic"]}),
                "palette_budget": ("INT", {"default": 0, "min": 0, "max": 256}),
                "padding_x": ("INT", {"default": -1, "min": -1, "max": 256}),
                "padding_y": ("INT", {"default": -1, "min": -1, "max": 256}),
                "variants": ("BOOLEAN", {"default": False}),
                "enabled": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "mask": ("MASK",),
                "normal_mask": ("MASK",),
                "target_size": ("INT", {"default": 0, "min": 0, "max": 512}),
                "outline": ("BOOLEAN", {"default": True}),
                "outline_color": ("STRING", {"default": "#000000"}),
                "sequence_mode": ("STRING", {"default": "auto", "enum": ["auto", "independent", "chunk", "continuous"]}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "IMAGE")
    RETURN_NAMES = ("image", "mask", "normal")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Pixel"

    def run(
        self,
        image,
        normal,
        target_width,
        target_height,
        profile="production",
        palette_budget=0,
        padding_x=-1,
        padding_y=-1,
        variants=False,
        enabled=True,
        mask=None,
        normal_mask=None,
        target_size=0,
        outline=True,
        outline_color="#000000",
        sequence_mode="auto",
    ):
        if not enabled:
            passthrough_mask = mask
            if passthrough_mask is None:
                passthrough_mask = torch.ones(
                    image.shape[0], image.shape[1], image.shape[2], device=image.device, dtype=image.dtype
                )
            return (image, passthrough_mask, normal)
        from .pixel.adapter import pixelize_pair_batch

        output, output_mask, output_normal = pixelize_pair_batch(
            image.detach().cpu().numpy(),
            normal.detach().cpu().numpy(),
            mask.detach().cpu().numpy() if mask is not None else None,
            normal_mask.detach().cpu().numpy() if normal_mask is not None else None,
            int(target_width),
            int(target_height),
            profile=str(profile),
            palette_budget=int(palette_budget),
            padding_x=int(padding_x),
            padding_y=int(padding_y),
            variants=bool(variants),
            target_size=int(target_size) if int(target_size) > 0 else None,
            outline=bool(outline),
            outline_color=str(outline_color),
            sequence_mode=str(sequence_mode),
        )
        return (
            torch.from_numpy(output).to(dtype=image.dtype),
            torch.from_numpy(output_mask).to(dtype=image.dtype),
            torch.from_numpy(output_normal).to(dtype=normal.dtype),
        )


class CS_PixelizeSequence:
    """Stream up to 240 bridge-loaded frames through Pixel Compiler v2."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source": ("CS_FRAMESEQ",),
                "target_width": ("INT", {"default": 128, "min": 16, "max": 512}),
                "target_height": ("INT", {"default": 128, "min": 16, "max": 512}),
                "profile": (
                    "STRING",
                    {"default": "production", "enum": ["production", "fidelity", "balanced", "graphic"]},
                ),
                "palette_budget": ("INT", {"default": 0, "min": 0, "max": 256}),
                "padding_x": ("INT", {"default": -1, "min": -1, "max": 256}),
                "padding_y": ("INT", {"default": -1, "min": -1, "max": 256}),
                "temporal_mode": (
                    "STRING",
                    {"default": "auto", "enum": ["auto", "shared", "flow", "independent"]},
                ),
            },
            "optional": {
                "target_size": ("INT", {"default": 0, "min": 0, "max": 512}),
                "outline": ("BOOLEAN", {"default": True}),
                "outline_color": ("STRING", {"default": "#000000"}),
            },
        }

    RETURN_TYPES = ("CS_PIXEL_SEQUENCE", "CS_PIXEL_PLAN")
    RETURN_NAMES = ("frames", "pixel_plan")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Pixel"

    def run(
        self,
        source,
        target_width,
        target_height,
        profile="production",
        palette_budget=0,
        padding_x=-1,
        padding_y=-1,
        temporal_mode="auto",
        target_size=0,
        outline=True,
        outline_color="#000000",
    ):
        from .pixel.adapter import pixelize_sequence_reader

        count = len(getattr(source, "frames", ()) or ())
        progress_bar = None
        try:  # ComfyUI runtime only; unit tests keep the node dependency-light.
            from comfy.utils import ProgressBar

            progress_bar = ProgressBar(max(1, count * 2))
        except (ImportError, AttributeError):  # pragma: no cover - absent outside ComfyUI.
            pass

        def progress(stage, current, total):
            if progress_bar is None:
                return
            offset = 0 if stage == "分析全局几何与调色板" else count
            # ComfyUI's third ``ProgressBar.update_absolute`` argument is an
            # image preview, not a status string.  The API derives the two
            # user-visible phases from this single standard progress range.
            progress_bar.update_absolute(min(count * 2, offset + int(current)), count * 2)

        result = pixelize_sequence_reader(
            source,
            int(target_width),
            int(target_height),
            profile=str(profile),
            palette_budget=int(palette_budget),
            padding_x=int(padding_x),
            padding_y=int(padding_y),
            target_size=int(target_size) if int(target_size) > 0 else None,
            outline=bool(outline),
            outline_color=str(outline_color),
            temporal_mode=str(temporal_mode),
            progress=progress,
        )
        return (result, result.plan)


class CS_ProjectNormalToPixelPlan:
    """Apply a verified PixelGeometryPlan to a single Lotus normal result."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source": ("IMAGE",),
                "normal": ("IMAGE",),
                "pixel_plan": ("CS_PIXEL_PLAN",),
                "frame_index": ("INT", {"default": 0, "min": 0, "max": 239}),
            },
            "optional": {"mask": ("MASK",)},
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("normal", "mask")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Normal"

    def run(self, source, normal, pixel_plan, frame_index, mask=None):
        from .pixel.adapter import project_normal_to_pixel_plan

        output, output_mask = project_normal_to_pixel_plan(
            source.detach().cpu().numpy(),
            normal.detach().cpu().numpy(),
            mask.detach().cpu().numpy() if mask is not None else None,
            pixel_plan,
            int(frame_index),
        )
        return (
            torch.from_numpy(output).to(dtype=normal.dtype),
            torch.from_numpy(output_mask).to(dtype=normal.dtype),
        )


class CS_PixelSnap:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "grid_mode": ("STRING", {"default": "auto", "enum": ["auto", "manual"]}),
                "pixel_size_x": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 64.0}),
                "pixel_size_y": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 64.0}),
                "phase_x": ("FLOAT", {"default": 0.0, "min": -64.0, "max": 64.0}),
                "phase_y": ("FLOAT", {"default": 0.0, "min": -64.0, "max": 64.0}),
                "constrained_warp": ("BOOLEAN", {"default": False}),
                "palette_budget": ("INT", {"default": 32, "min": 2, "max": 256}),
                "target_width": ("INT", {"default": 0, "min": 0, "max": 512}),
                "target_height": ("INT", {"default": 0, "min": 0, "max": 512}),
            },
            "optional": {"mask": ("MASK",)},
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Pixel"

    def run(self, image, grid_mode, pixel_size_x, pixel_size_y, phase_x, phase_y, constrained_warp, palette_budget, target_width, target_height, mask=None):
        from .pixel.adapter import snap_batch

        output, output_mask = snap_batch(
            image.detach().cpu().numpy(),
            mask.detach().cpu().numpy() if mask is not None else None,
            str(grid_mode),
            float(pixel_size_x),
            float(pixel_size_y),
            float(phase_x),
            float(phase_y),
            bool(constrained_warp),
            int(palette_budget),
            int(target_width),
            int(target_height),
        )
        return (
            torch.from_numpy(output).to(device=image.device, dtype=image.dtype),
            torch.from_numpy(output_mask).to(device=image.device, dtype=image.dtype),
        )


class CS_RemoveBackground:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "model": ("STRING", {"default": "u2net", "enum": ["u2net", "u2netp", "isnet-anime", "birefnet-general"]}),
                "alpha_matting": ("BOOLEAN", {"default": False}),
                "alpha_matting_foreground_threshold": ("INT", {"default": 240, "min": 0, "max": 255}),
                "alpha_matting_background_threshold": ("INT", {"default": 10, "min": 0, "max": 255}),
                "alpha_matting_erode_size": ("INT", {"default": 10, "min": 0, "max": 64}),
                "batch_size": ("INT", {"default": 1, "min": 1, "max": 64}),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Alpha"

    def run(self, image, model, alpha_matting, alpha_matting_foreground_threshold, alpha_matting_background_threshold, alpha_matting_erode_size, batch_size):
        from .alpha import remove_background_batch

        output, output_mask = remove_background_batch(
            image.detach().cpu().numpy(),
            str(model),
            bool(alpha_matting),
            int(alpha_matting_foreground_threshold),
            int(alpha_matting_background_threshold),
            int(alpha_matting_erode_size),
            int(batch_size),
        )
        return (
            torch.from_numpy(output).to(device=image.device, dtype=image.dtype),
            torch.from_numpy(output_mask).to(device=image.device, dtype=image.dtype),
        )


class CS_IsolateOnGreen:
    """Replace edge-connected background colors with canonical chroma green."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "tolerance": ("FLOAT", {"default": 0.22, "min": 0.02, "max": 0.6}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "run"
    CATEGORY = "CookSprite/Media"

    def run(self, image, tolerance):
        from scipy.ndimage import binary_propagation

        results = []
        for frame in image:
            pixels = frame[..., :3].detach().cpu().numpy().copy()
            border = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]), axis=0)
            background_color = np.median(border, axis=0)
            distance = np.linalg.norm(pixels - background_color, axis=2)
            candidate = distance <= float(tolerance)
            seed = np.zeros(candidate.shape, dtype=bool)
            seed[0] = candidate[0]
            seed[-1] = candidate[-1]
            seed[:, 0] = candidate[:, 0]
            seed[:, -1] = candidate[:, -1]
            background = binary_propagation(seed, mask=candidate)
            pixels[background] = np.array([0.0, 1.0, 0.0], dtype=np.float32)
            results.append(torch.from_numpy(pixels).to(frame.device, dtype=frame.dtype))
        return (torch.stack(results),)


class CS_LotusModelLoader:
    """Load the official Lotus model in its validated BF16 precision."""

    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths

        models = folder_paths.get_filename_list("diffusion_models")
        return {
            "required": {
                "model_name": (
                    models,
                    {"default": "lotus-normal-d-v1-1.safetensors"},
                )
            }
        }

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "load"
    CATEGORY = "CookSprite/Normal"

    def load(self, model_name):
        if torch is None:
            raise RuntimeError("Lotus model loading requires ComfyUI's torch runtime")
        import comfy.sd
        import folder_paths

        path = folder_paths.get_full_path_or_raise("diffusion_models", model_name)
        return (
            comfy.sd.load_diffusion_model(
                path,
                model_options={"dtype": torch.bfloat16},
            ),
        )


def _normal_mask(image, mask):
    """Normalize an optional Comfy mask to the IMAGE batch and source size."""

    from torch.nn import functional

    batch, height, width = image.shape[:3]
    if mask is None:
        return torch.ones((batch, height, width), device=image.device, dtype=image.dtype)
    value = mask.to(device=image.device, dtype=image.dtype)
    if value.ndim == 2:
        value = value.unsqueeze(0)
    if value.shape[0] == 1 and batch > 1:
        value = value.repeat(batch, 1, 1)
    if value.shape[0] != batch:
        raise ValueError("Lotus mask batch must match IMAGE batch")
    if value.shape[1:3] != (height, width):
        value = functional.interpolate(
            value.unsqueeze(1), size=(height, width), mode="nearest"
        ).squeeze(1)
    return value.clamp(0.0, 1.0)


def _lotus_size(height: int, width: int, edge: int = 768) -> tuple[int, int]:
    """Match Lotus' 768 longest-edge preprocessing while retaining aspect."""

    scale = float(edge) / float(max(height, width))
    target_height = max(8, round(height * scale / 8.0) * 8)
    target_width = max(8, round(width * scale / 8.0) * 8)
    return target_height, target_width


def _lotus_normal_axes(raw, strength: float, flip_y: bool):
    """Convert Lotus camera normals to CookSprite's image-plane tangent basis.

    Lotus v1.1 aligns camera-space normals with the camera ray.  CookSprite's
    canonical normal map instead describes an outward-facing sprite plane with
    tangent +X to image-right, +Y to image-up and +Z towards the viewer.  The
    two image-aligned bases therefore differ by the Lotus normal orientation
    and camera/image handedness, which reduces to a fixed X sign change.
    """

    nx = -raw[..., 0] * float(strength)
    ny = raw[..., 1] * float(strength)
    if flip_y:
        ny = -ny
    return nx, ny, raw[..., 2]


class CS_LotusNormalPrepare:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"image": ("IMAGE",)},
            "optional": {"mask": ("MASK",)},
        }

    RETURN_TYPES = ("IMAGE", "MASK", "IMAGE")
    RETURN_NAMES = ("image", "mask", "reference")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Normal"

    def run(self, image, mask=None):
        if torch is None:
            raise RuntimeError("Lotus preprocessing requires ComfyUI's torch runtime")
        from torch.nn import functional

        rgb = image[..., :3]
        alpha = _normal_mask(rgb, mask)
        # Grow foreground colors under transparent edge pixels before resize.
        # This prevents dark RGB in transparent texels from bleeding into the
        # Lotus input without changing the authoritative source alpha.
        filled = rgb.permute(0, 3, 1, 2)
        known = (alpha > 1e-4).unsqueeze(1).to(rgb.dtype)
        for _ in range(8):
            weights = functional.avg_pool2d(known, 3, stride=1, padding=1)
            colors = functional.avg_pool2d(filled * known, 3, stride=1, padding=1)
            candidates = colors / weights.clamp_min(1e-6)
            grow = (known < 0.5) & (weights > 0.0)
            filled = torch.where(grow.expand_as(filled), candidates, filled)
            known = torch.where(grow, torch.ones_like(known), known)
        neutral = torch.full_like(filled, 0.5)
        background = torch.where(known > 0.5, filled, neutral)
        source = rgb.permute(0, 3, 1, 2)
        composed = source * alpha.unsqueeze(1) + background * (1.0 - alpha.unsqueeze(1))
        target = _lotus_size(rgb.shape[1], rgb.shape[2])
        prepared = functional.interpolate(
            composed,
            size=target,
            mode="bilinear",
            align_corners=False,
            antialias=True,
        ).permute(0, 2, 3, 1)
        return (prepared.clamp(0.0, 1.0), alpha, rgb)


class CS_LotusNormalFinalize:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prediction": ("IMAGE",),
                "reference": ("IMAGE",),
                "strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 4.0}),
                "flip_y": ("BOOLEAN", {"default": False}),
            },
            "optional": {"mask": ("MASK",)},
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("normal", "mask")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Normal"

    def run(self, prediction, reference, strength, flip_y, mask=None):
        if torch is None:
            raise RuntimeError("Lotus postprocessing requires ComfyUI's torch runtime")
        from torch.nn import functional

        batch, height, width = reference.shape[:3]
        value = prediction[..., :3]
        if value.shape[0] == 1 and batch > 1:
            value = value.repeat(batch, 1, 1, 1)
        if value.shape[0] != batch:
            raise ValueError("Lotus output batch must match the reference batch")
        if value.shape[1:3] != (height, width):
            value = functional.interpolate(
                value.permute(0, 3, 1, 2),
                size=(height, width),
                mode="nearest",
            ).permute(0, 2, 3, 1)

        raw = value * 2.0 - 1.0
        # Convert once at the compute boundary.  Every persisted CookSprite
        # NormalMap is then the same OpenGL-style tangent-space artifact used
        # by Three.js and Godot Sprite2D; clients need no model-specific fix.
        nx, ny, nz = _lotus_normal_axes(raw, strength, flip_y)
        normal = functional.normalize(torch.stack((nx, ny, nz), dim=-1), dim=-1)
        encoded = normal * 0.5 + 0.5
        alpha = _normal_mask(reference, mask)
        neutral = torch.tensor(
            [0.5, 0.5, 1.0], device=encoded.device, dtype=encoded.dtype
        ).view(1, 1, 1, 3)
        output = torch.where((alpha > 1e-4).unsqueeze(-1), encoded, neutral)
        return (output.clamp(0.0, 1.0), alpha)


class CS_SliceSpriteSheet:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "columns": ("INT", {"default": 0, "min": 0, "max": 256}),
                "rows": ("INT", {"default": 0, "min": 0, "max": 256}),
                "frame_width": ("INT", {"default": 64, "min": 1, "max": 4096}),
                "frame_height": ("INT", {"default": 64, "min": 1, "max": 4096}),
                "margin": ("INT", {"default": 0, "min": 0, "max": 512}),
                "spacing": ("INT", {"default": 0, "min": 0, "max": 512}),
                "exclude_empty": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "run"
    CATEGORY = "CookSprite/Media"

    def run(
        self,
        image,
        columns,
        rows,
        frame_width,
        frame_height,
        margin,
        spacing,
        exclude_empty,
    ):
        source = image[0]
        height, width = source.shape[:2]
        frame_width = int(frame_width)
        frame_height = int(frame_height)
        margin = int(margin)
        spacing = int(spacing)
        columns = int(columns) or max(1, (width - 2 * margin + spacing) // (frame_width + spacing))
        rows = int(rows) or max(1, (height - 2 * margin + spacing) // (frame_height + spacing))
        frames = []
        for row in range(rows):
            for column in range(columns):
                x = margin + column * (frame_width + spacing)
                y = margin + row * (frame_height + spacing)
                frame = source[y : y + frame_height, x : x + frame_width]
                if frame.shape[0] != frame_height or frame.shape[1] != frame_width:
                    continue
                if exclude_empty and float(frame.abs().max()) < 1e-6:
                    continue
                frames.append(frame)
        if not frames:
            raise ValueError("the configured grid did not contain any complete frames")
        return (torch.stack(frames),)


class CS_LoadVideoArtifact:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("STRING", {"multiline": False}),
                "sample_fps": ("FLOAT", {"default": 12.0, "min": 0.1, "max": 120.0}),
                "max_frames": ("INT", {"default": 48, "min": 1, "max": 1000}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "load"
    CATEGORY = "CookSprite/Bridge"

    def load(self, video, sample_fps, max_frames):
        import imageio.v2 as imageio

        data, media_type = _read(video)
        suffix = ".gif" if media_type == "image/gif" else ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
            handle.write(data)
            handle.flush()
            reader = imageio.get_reader(handle.name)
            metadata = reader.get_meta_data()
            source_fps = float(metadata.get("fps") or sample_fps or 1)
            stride = max(1, round(source_fps / max(float(sample_fps), 0.1)))
            frames = []
            for index, frame in enumerate(reader):
                if index % stride:
                    continue
                frames.append(_tensor(Image.fromarray(frame).convert("RGB"))[0])
                if len(frames) >= int(max_frames):
                    break
            reader.close()
        if not frames:
            raise ValueError("video contains no decodable frames")
        return (torch.stack(frames),)


class CS_MakeSpritePair:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"diffuse": ("IMAGE",), "normal": ("IMAGE",)}}

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("diffuse", "normal")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Media"

    def run(self, diffuse, normal):
        return (diffuse, normal)


NODE_CLASSES = [
    CS_LoadArtifact,
    CS_StoreArtifact,
    CS_IsolateOnGreen,
    CS_Pixelize,
    CS_PixelizePair,
    CS_PixelizeSequence,
    CS_PixelSnap,
    CS_RemoveBackground,
    CS_LotusModelLoader,
    CS_LotusNormalPrepare,
    CS_LotusNormalFinalize,
    CS_NormalCrafterSequence,
    CS_NormalCrafterBatch,
    CS_ProjectNormalToPixelPlan,
    CS_SliceSpriteSheet,
    CS_LoadVideoArtifact,
    CS_MakeSpritePair,
]
NODE_CLASS_MAPPINGS = {node.__name__: node for node in NODE_CLASSES}
NODE_DISPLAY_NAME_MAPPINGS = {
    key: key.replace("CS_", "CookSprite: ") for key in NODE_CLASS_MAPPINGS
}

# ComfyUI imports a custom-node package once during startup.  Register after
# all module definitions so the endpoint cannot expose an incompletely loaded
# package if a later node import fails.
_register_runtime_info_route()
