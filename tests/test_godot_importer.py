from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

GODOT = Path("/Applications/Godot.app/Contents/MacOS/Godot")
DIRECTIONS = ("n", "ne", "e", "se", "s", "sw", "w", "nw")


def png() -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (2, 2), (180, 80, 220, 255)).save(output, "PNG")
    return output.getvalue()


def package(path: Path, manifest: dict, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("provenance.json", "{}")
        for name, body in files.items():
            archive.writestr(name, body)


@pytest.mark.skipif(not GODOT.exists(), reason="Godot editor is not installed")
def test_godot_imports_static_sixteen_tracks_and_tileset(tmp_path):
    project = tmp_path / "godot"
    shutil.copytree(Path(__file__).parents[1] / "godot", project)
    fixtures = project / "tests" / "fixtures"
    fixtures.mkdir()
    image = png()
    base = {
        "schema": "cooksprite.package/v1",
        "canvas": {"width": 2, "height": 2},
        "normal_coordinate": "opengl",
        "integrity_warnings": [],
    }
    package(
        fixtures / "static.cooksprite",
        {
            **base,
            "type": "static",
            "pivot": {"x": 0.5, "y": 1},
            "primary": "frames/primary.png",
            "normal": "normals/primary.png",
        },
        {"frames/primary.png": image, "normals/primary.png": image},
    )
    files = {}

    def track(direction: str):
        paths = []
        for index in range(2):
            stem = f"{direction}_{index}"
            files[f"frames/{stem}.png"] = image
            files[f"normals/{stem}.png"] = image
            paths.append(
                {
                    "diffuse": f"frames/{stem}.png",
                    "normal": f"normals/{stem}.png",
                    "duration_ms": 80,
                    "offset": {"x": index, "y": 0},
                }
            )
        return {"direction": direction, "frames": paths}

    clips = [
        {
            "id": "walk",
            "name": "walk",
            "action": "walk",
            "loop": "linear",
            "views": [{"id": "level", "tracks": [track(item) for item in DIRECTIONS]}],
        },
        {
            "id": "attack",
            "name": "attack",
            "action": "attack",
            "loop": "pingpong",
            "views": [{"id": "top45", "tracks": [track(item) for item in DIRECTIONS]}],
        },
    ]
    package(
        fixtures / "character.cooksprite",
        {**base, "type": "character", "pivot": {"x": 0.5, "y": 1}, "clips": clips},
        files,
    )
    package(
        fixtures / "tileset.cooksprite",
        {
            **base,
            "type": "tileset",
            "source": "frames/tileset.png",
            "normal": "normals/tileset.png",
            "tile_width": 1,
            "tile_height": 1,
            "margin": 0,
            "spacing": 0,
        },
        {"frames/tileset.png": image, "normals/tileset.png": image},
    )
    imported = subprocess.run(
        [str(GODOT), "--headless", "--editor", "--path", str(project), "--import", "--quit"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert imported.returncode == 0, imported.stdout + imported.stderr
    verified = subprocess.run(
        [
            str(GODOT),
            "--headless",
            "--path",
            str(project),
            "--script",
            "res://tests/import_assert.gd",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "COOKSPRITE_GODOT_IMPORT_OK" in verified.stdout
