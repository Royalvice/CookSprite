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

import numpy as np
from PIL import Image

try:  # Prompt Tool tests and API tooling do not need the compute-only torch dependency.
    import torch
except ImportError:  # pragma: no cover - ComfyUI always supplies torch at node runtime.
    torch = None

from .prompting import (
    DEFAULT_GREEN_SCREEN_BACKGROUND,
    ImagePromptRequest,
    ModelFamily,
    SpritePromptCompiler,
    VideoPromptRequest,
)


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


def _post(url: str, data: bytes):
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "image/png", "User-Agent": "CookSprite-Comfy/1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _append_query(url: str, **values) -> str:
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return url + separator + urllib.parse.urlencode(values)


class CS_LoadArtifact:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"artifact_url": ("STRING", {"multiline": False})}}

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "load"
    CATEGORY = "CookSprite/Bridge"

    def load(self, artifact_url):
        data, _ = _read(artifact_url)
        image = Image.open(io.BytesIO(data))
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            alpha = np.asarray(rgba, dtype=np.uint8)[..., 3].astype(np.float32) / 255.0
            image = rgba.convert("RGB")
        else:
            image = image.convert("RGB")
            alpha = np.ones((image.height, image.width), dtype=np.float32)
        return (_tensor(image), torch.from_numpy(alpha).unsqueeze(0))


class CS_StoreArtifact:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "value": ("IMAGE",),
                "upload_url": ("STRING", {"multiline": False}),
            },
            "optional": {"mask": ("MASK",)},
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "store"
    CATEGORY = "CookSprite/Bridge"
    OUTPUT_NODE = True

    def store(self, value, upload_url, mask=None):
        refs = []
        kind = urllib.parse.parse_qs(urllib.parse.urlparse(upload_url).query).get(
            "kind", ["Image"]
        )[0]
        mask_array = _array(mask) if mask is not None else None
        for index, frame in enumerate(value):
            frame_mask = None
            if mask_array is not None:
                frame_mask = mask_array[index if mask_array.ndim == 3 else 0]
            body = _post(
                _append_query(upload_url, output_index=index),
                _png(frame, kind, frame_mask),
            )
            refs.append(json.loads(body.decode())["id"])
        return (json.dumps(refs),)


class CS_CompilePromptPacket:
    """Compile a model-neutral CookSprite prompt packet inside ComfyUI.

    The seven required fields are the original node contract.  The optional
    scalar fields extend it without invalidating already-saved Comfy graphs.
    No model, CLIP encoder, or API call is touched here.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "action_id": ("STRING", {"default": "image.generate"}),
                "prompt": ("STRING", {"default": "", "multiline": True}),
                "category": ("STRING", {"default": "character"}),
                "style": ("STRING", {"default": "2d_action_game"}),
                "animation": ("STRING", {"default": "idle"}),
                "view": ("STRING", {"default": "level"}),
                "direction": ("STRING", {"default": "s"}),
            },
            "optional": {
                "task": ("STRING", {"default": "image"}),
                "mode": ("STRING", {"default": "t2i"}),
                "caption": ("STRING", {"default": "", "multiline": True}),
                "action": ("STRING", {"default": "idle"}),
                "camera_option": ("STRING", {"default": "front_eye_level"}),
                "camera_preset": ("STRING", {"default": "eye_level"}),
                "orientation": ("STRING", {"default": "front"}),
                "facing": ("STRING", {"default": "right"}),
                "model": ("STRING", {"default": "generic"}),
                "width": ("INT", {"default": 512, "min": 1, "max": 8192}),
                "height": ("INT", {"default": 512, "min": 1, "max": 8192}),
                "background": ("STRING", {"default": DEFAULT_GREEN_SCREEN_BACKGROUND}),
                "edit_instruction": ("STRING", {"default": "", "multiline": True}),
                "compile_prompt": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "negative_prompt", "metadata")
    FUNCTION = "compile"
    CATEGORY = "CookSprite/Prompt"

    def compile(
        self,
        action_id,
        prompt,
        category,
        style,
        animation,
        view,
        direction,
        task="image",
        mode="t2i",
        caption="",
        action="",
        camera_option="front_eye_level",
        camera_preset="eye_level",
        orientation="front",
        facing="right",
        model="generic",
        width=512,
        height=512,
        background=DEFAULT_GREEN_SCREEN_BACKGROUND,
        edit_instruction="",
        compile_prompt=True,
        **_legacy,
    ):
        if not compile_prompt:
            raw_prompt = str(prompt if prompt is not None else caption or "")
            metadata = json.dumps(
                {
                    "compiler_enabled": False,
                    "task": str(task or "image").strip().lower(),
                    "mode": str(mode or "t2i").strip().lower(),
                    "prompt": raw_prompt,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            return (raw_prompt, "", metadata)
        compiler = SpritePromptCompiler()
        task_value = str(task or "").strip().lower()
        action_value = str(action or animation or "idle").strip().lower()
        caption_value = str(caption or prompt or category or "game sprite asset").strip()
        if task_value == "video" or str(action_id).startswith("animation"):
            result = compiler.compile_video(
                VideoPromptRequest(
                    caption=caption_value,
                    action=action_value,
                    mode=mode or "i2v",
                    orientation=orientation or "front",
                    facing=facing or "right",
                    camera_preset=camera_preset or ("top45" if view == "top45" else "level"),
                    direction={
                        "n": "away_from_camera",
                        "s": "in_place",
                    }.get(str(direction), "in_place"),
                    model=model or ModelFamily.GENERIC.value,
                    resolution=(int(width), int(height)),
                    background=background or DEFAULT_GREEN_SCREEN_BACKGROUND,
                )
            )
        else:
            result = compiler.compile_image(
                ImagePromptRequest(
                    caption=caption_value,
                    mode=mode or "t2i",
                    style=style or "pixel",
                    category=category or "character",
                    # Image generation has one product camera contract. The
                    # optional legacy camera inputs remain in the node schema
                    # so saved graphs still load, but cannot change output.
                    camera_option="front_eye_level",
                    camera_preset="eye_level",
                    orientation="front",
                    facing="right",
                    resolution=(int(width), int(height)),
                    background=background or DEFAULT_GREEN_SCREEN_BACKGROUND,
                    edit_instruction=edit_instruction or None,
                )
            )
        metadata = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True)
        return (result.prompt, "", metadata)


# Semantic development alias.  It is intentionally not a second Comfy node.
CS_PromptEnhance = CS_CompilePromptPacket


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
        )
        return (
            torch.from_numpy(output).to(device=image.device, dtype=image.dtype),
            torch.from_numpy(output_mask).to(device=image.device, dtype=image.dtype),
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
    CS_CompilePromptPacket,
    CS_IsolateOnGreen,
    CS_Pixelize,
    CS_PixelSnap,
    CS_RemoveBackground,
    CS_LotusModelLoader,
    CS_LotusNormalPrepare,
    CS_LotusNormalFinalize,
    CS_SliceSpriteSheet,
    CS_LoadVideoArtifact,
    CS_MakeSpritePair,
]
NODE_CLASS_MAPPINGS = {node.__name__: node for node in NODE_CLASSES}
NODE_DISPLAY_NAME_MAPPINGS = {
    key: key.replace("CS_", "CookSprite: ") for key in NODE_CLASS_MAPPINGS
}
