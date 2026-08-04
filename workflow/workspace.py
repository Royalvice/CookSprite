"""Lightweight workspace: a folder + YAML config + YAML manifest.

Layout:
    <workspace>/
      cooksprite.yaml          # workspace config (general params, overridable)
      artifacts/<id>.png       # saved diffuse / normal / sheet images
      manifest.yaml            # append-only record of runs and their artifacts

The file system stays transparent — no hidden database. General params
(canvas size, direction count, naming, pivot) live in the config and are
inherited by every run; a run may override them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .codec import encode_png_bytes
from .types import Artifact, Image, NormalMap, SpritePair, SpriteSheet

CONFIG_NAME = "cooksprite.yaml"
MANIFEST_NAME = "manifest.yaml"
ARTIFACT_DIR = "artifacts"

DEFAULT_CONFIG: dict[str, Any] = {
    "canvas": {"width": 96, "height": 96},
    "pivot": "bottom_center",
    "normal": {"enabled": True},
    "naming": {"prefix": "sprite"},
}


@dataclass
class Workspace:
    root: Path
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def init(cls, root: str | Path) -> "Workspace":
        root = Path(root)
        (root / ARTIFACT_DIR).mkdir(parents=True, exist_ok=True)
        cfg_path = root / CONFIG_NAME
        if not cfg_path.exists():
            cfg_path.write_text(yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False))
        man_path = root / MANIFEST_NAME
        if not man_path.exists():
            man_path.write_text(yaml.safe_dump({"runs": []}, sort_keys=False))
        return cls.load(root)

    @classmethod
    def load(cls, root: str | Path) -> "Workspace":
        root = Path(root)
        cfg_path = root / CONFIG_NAME
        config = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else dict(DEFAULT_CONFIG)
        return cls(root=root, config=config or {})

    # --- config-derived defaults for workflow params -----------------------

    def param_defaults(self) -> dict[str, Any]:
        """Flatten the config into workflow-param defaults."""
        canvas = self.config.get("canvas", {})
        defaults: dict[str, Any] = {}
        if "width" in canvas:
            defaults["width"] = canvas["width"]
        if "height" in canvas:
            defaults["height"] = canvas["height"]
        normal = self.config.get("normal", {})
        if "enabled" in normal:
            defaults["normal"] = normal["enabled"]
        return defaults

    # --- artifact persistence ---------------------------------------------

    def _artifact_path(self, artifact_id: str) -> Path:
        return self.root / ARTIFACT_DIR / f"{artifact_id}.png"

    def save_artifact(self, artifact: Artifact) -> dict[str, Any]:
        """Persist a typed artifact's images to PNG and return a manifest entry
        (with kind, ids, and geometry) usable by the API/CLI."""
        entry: dict[str, Any] = {"id": uuid.uuid4().hex, "kind": artifact.kind}
        if isinstance(artifact, SpritePair):
            entry["diffuse"] = self._write(artifact.diffuse.pixels)
            if artifact.normal is not None:
                entry["normal"] = self._write(artifact.normal.pixels)
        elif isinstance(artifact, SpriteSheet):
            entry["diffuse"] = self._write(artifact.diffuse.pixels)
            entry["frames"] = artifact.frames
            entry["frame_w"] = artifact.frame_w
            entry["frame_h"] = artifact.frame_h
            if artifact.normal is not None:
                entry["normal"] = self._write(artifact.normal.pixels)
        elif isinstance(artifact, (Image, NormalMap)):
            entry["image"] = self._write(artifact.pixels)
        else:
            raise ValueError(f"cannot persist artifact kind {artifact.kind}")
        return entry

    def _write(self, pixels) -> str:
        aid = uuid.uuid4().hex
        self._artifact_path(aid).write_bytes(encode_png_bytes(pixels))
        return aid

    def artifact_bytes(self, artifact_id: str) -> bytes:
        return self._artifact_path(artifact_id).read_bytes()

    def record_run(self, run: dict[str, Any]) -> None:
        man_path = self.root / MANIFEST_NAME
        manifest = yaml.safe_load(man_path.read_text()) if man_path.exists() else {"runs": []}
        manifest.setdefault("runs", []).append(run)
        man_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
