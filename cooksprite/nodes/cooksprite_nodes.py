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
import torch
from PIL import Image

NODE_PACK_VERSION = "1.1.0"


def _tensor(image: Image.Image):
    pixels = np.array(image, dtype=np.float32, copy=True) / 255.0
    return torch.from_numpy(pixels).unsqueeze(0)


def _png(value, kind):
    array = (value.detach().cpu().numpy().clip(0, 1) * 255).astype("uint8")
    image = Image.fromarray(array).convert("RGB")
    if kind != "NormalMap":
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

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "load"
    CATEGORY = "CookSprite/Bridge"

    def load(self, artifact_url):
        data, _ = _read(artifact_url)
        image = Image.open(io.BytesIO(data))
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (0, 255, 0, 255))
            background.alpha_composite(rgba)
            image = background.convert("RGB")
        else:
            image = image.convert("RGB")
        return (_tensor(image),)


class CS_StoreArtifact:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "value": ("IMAGE",),
                "upload_url": ("STRING", {"multiline": False}),
            }
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "store"
    CATEGORY = "CookSprite/Bridge"
    OUTPUT_NODE = True

    def store(self, value, upload_url):
        refs = []
        kind = urllib.parse.parse_qs(urllib.parse.urlparse(upload_url).query).get(
            "kind", ["Image"]
        )[0]
        for index, frame in enumerate(value):
            body = _post(_append_query(upload_url, output_index=index), _png(frame, kind))
            refs.append(json.loads(body.decode())["id"])
        return (json.dumps(refs),)


class CS_Pixelize:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "target_width": ("INT", {"default": 128, "min": 8, "max": 4096}),
                "target_height": ("INT", {"default": 128, "min": 8, "max": 4096}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "run"
    CATEGORY = "CookSprite/Media"

    def run(self, image, target_width, target_height):
        frames = []
        for frame in image:
            source = Image.open(io.BytesIO(_png(frame, "NormalMap"))).convert("RGB")
            result = source.resize(
                (int(target_width), int(target_height)), Image.Resampling.NEAREST
            )
            frames.append(_tensor(result)[0])
        return (torch.stack(frames),)


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


class CS_CenterAlign:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",)}}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "run"
    CATEGORY = "CookSprite/Media"

    def run(self, image):
        return (image,)


class CS_NormalEstimate:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 4.0}),
                "flip_y": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "run"
    CATEGORY = "CookSprite/Media"

    def run(self, image, strength, flip_y):
        rgb = image[..., :3]
        gray = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
        dx = torch.zeros_like(gray)
        dy = torch.zeros_like(gray)
        dx[:, :, 1:-1] = (gray[:, :, 2:] - gray[:, :, :-2]) * 0.5
        dx[:, :, 0] = gray[:, :, 1] - gray[:, :, 0]
        dx[:, :, -1] = gray[:, :, -1] - gray[:, :, -2]
        dy[:, 1:-1, :] = (gray[:, 2:, :] - gray[:, :-2, :]) * 0.5
        dy[:, 0, :] = gray[:, 1, :] - gray[:, 0, :]
        dy[:, -1, :] = gray[:, -1, :] - gray[:, -2, :]
        ny = dy * float(strength) * (1.0 if flip_y else -1.0)
        nx = -dx * float(strength)
        nz = torch.ones_like(nx)
        normal = torch.stack((nx, ny, nz), dim=-1)
        normal = torch.nn.functional.normalize(normal, dim=-1)
        return (normal * 0.5 + 0.5,)


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
                "video_url": ("STRING", {"multiline": False}),
                "sample_fps": ("FLOAT", {"default": 12.0, "min": 0.1, "max": 120.0}),
                "max_frames": ("INT", {"default": 48, "min": 1, "max": 1000}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "load"
    CATEGORY = "CookSprite/Bridge"

    def load(self, video_url, sample_fps, max_frames):
        import imageio.v2 as imageio

        data, media_type = _read(video_url)
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
    CS_CenterAlign,
    CS_NormalEstimate,
    CS_SliceSpriteSheet,
    CS_LoadVideoArtifact,
    CS_MakeSpritePair,
]
NODE_CLASS_MAPPINGS = {node.__name__: node for node in NODE_CLASSES}
NODE_DISPLAY_NAME_MAPPINGS = {
    key: key.replace("CS_", "CookSprite: ") for key in NODE_CLASS_MAPPINGS
}
