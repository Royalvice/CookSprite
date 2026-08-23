"""Build the canonical .cooksprite package without buffering media in memory."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .store import Store

_COPY_CHUNK_BYTES = 1024 * 1024


class PackageError(ValueError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


@dataclass(frozen=True)
class PackageResult:
    """A fully flushed, content-addressed ZIP staging file ready for promotion."""

    staging_path: Path
    sha256: str
    size: int
    manifest: dict[str, Any]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_file(archive: zipfile.ZipFile, member: str, source: Path) -> None:
    """Write one media blob into a ZIP member in bounded-size chunks."""

    with source.open("rb") as input_handle, archive.open(
        member, mode="w", force_zip64=True
    ) as output_handle:
        shutil.copyfileobj(input_handle, output_handle, length=_COPY_CHUNK_BYTES)


def build_package(store: Store, project_id: str, allow_incomplete: bool = False) -> PackageResult:
    """Build a package directly inside the Blob Store staging area.

    The result is not a second project mirror and is not held in Python memory.
    Its caller must promote it with Store.put_artifact_file or remove the staged
    file on a later failure.
    """

    project = store.project(project_id)
    document_row = store.document(project_id)
    if not project or not document_row:
        raise PackageError(["project_not_found"])
    document = document_row["document"]
    issues: list[str] = []
    files: dict[str, Path] = {}
    provenance: dict[str, Any] = {"project": project_id, "artifacts": []}

    def include(artifact_id: str | None, folder: str, stem: str) -> str | None:
        if not artifact_id:
            return None
        row = store.artifact(artifact_id)
        if not row:
            issues.append(f"missing_artifact:{artifact_id}")
            return None
        if row["media_type"] != "image/png":
            issues.append(f"artifact_must_be_png:{artifact_id}")
            return None
        try:
            source = store.artifact_path(artifact_id)
        except FileNotFoundError:
            issues.append(f"missing_artifact_blob:{artifact_id}")
            return None
        path = str(PurePosixPath(folder) / f"{stem}.png")
        files[path] = source
        provenance["artifacts"].append(
            {
                "id": artifact_id,
                "sha256": row["sha256"],
                "kind": row["kind"],
                "meta": json.loads(row["meta"] or "{}"),
            }
        )
        return path

    manifest: dict[str, Any] = {
        "schema": "cooksprite.package/v1",
        "type": document["type"],
        "canvas": document.get("canvas", {"width": 64, "height": 64}),
        "normal_coordinate": "opengl",
        "integrity_warnings": issues,
    }
    if document["type"] == "static":
        static = document.get("static") or {}
        primary = include(static.get("primary"), "frames", "primary")
        normal = include(static.get("normal"), "normals", "primary")
        if not primary:
            issues.append("static_primary_missing")
        if not normal:
            issues.append("static_normal_missing")
        manifest.update(
            {
                "pivot": static.get("pivot", {"x": 0.5, "y": 1.0}),
                "primary": primary,
                "normal": normal,
            }
        )
    elif document["type"] == "tileset":
        tileset = document.get("tileset") or {}
        source = include(tileset.get("source"), "frames", "tileset")
        normal = include(tileset.get("normal"), "normals", "tileset")
        if not source:
            issues.append("tileset_source_missing")
        if not normal:
            issues.append("tileset_normal_missing")
        manifest.update(
            {
                "source": source,
                "normal": normal,
                "tile_width": tileset.get("tile_width", 32),
                "tile_height": tileset.get("tile_height", 32),
                "margin": tileset.get("margin", 0),
                "spacing": tileset.get("spacing", 0),
            }
        )
    else:
        character = document.get("character") or {}
        manifest["pivot"] = character.get("pivot", {"x": 0.5, "y": 1.0})
        manifest["clips"] = []
        for clip in character.get("clips", []):
            clip_out = {
                "id": clip["id"],
                "name": clip["name"],
                "action": clip["action"],
                "loop": clip.get("loop", "linear"),
                "views": [],
            }
            for view in clip.get("views", []):
                if not view.get("enabled", True):
                    continue
                tracks_out = []
                found_directions = {track["direction"] for track in view.get("tracks", [])}
                missing = {"n", "ne", "e", "se", "s", "sw", "w", "nw"} - found_directions
                if missing:
                    issues.append(
                        f"missing_directions:{clip['id']}:{view['id']}:{','.join(sorted(missing))}"
                    )
                for track in view.get("tracks", []):
                    frames_out = []
                    for index, frame in enumerate(track.get("frames", [])):
                        stem = f"{view['id']}_{clip['id']}_{track['direction']}_{index:03d}"
                        diffuse = include(frame.get("artifact"), "frames", stem)
                        normal = include(frame.get("normal"), "normals", stem)
                        if not normal:
                            issues.append(f"normal_missing:{stem}")
                        frames_out.append(
                            {
                                "diffuse": diffuse,
                                "normal": normal,
                                "duration_ms": frame.get("duration_ms", 100),
                                "offset": {
                                    "x": frame.get("offset_x", 0),
                                    "y": frame.get("offset_y", 0),
                                },
                            }
                        )
                    if not frames_out:
                        issues.append(f"empty_track:{clip['id']}:{view['id']}:{track['direction']}")
                    tracks_out.append({"direction": track["direction"], "frames": frames_out})
                clip_out["views"].append({"id": view["id"], "tracks": tracks_out})
            manifest["clips"].append(clip_out)
        if not manifest["clips"]:
            issues.append("character_clips_missing")
    manifest["integrity_warnings"] = sorted(set(issues))
    if issues and not allow_incomplete:
        raise PackageError(manifest["integrity_warnings"])

    staging = store.new_artifact_upload_path()
    try:
        with staging.open("xb") as handle:
            with zipfile.ZipFile(
                handle,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.writestr("provenance.json", json.dumps(provenance, ensure_ascii=False, indent=2))
                for path, source in sorted(files.items()):
                    _archive_file(archive, path, source)
            handle.flush()
            if os.name != "nt":
                os.fsync(handle.fileno())
        return PackageResult(
            staging_path=staging,
            sha256=_sha256_file(staging),
            size=staging.stat().st_size,
            manifest=manifest,
        )
    except Exception:
        staging.unlink(missing_ok=True)
        raise
