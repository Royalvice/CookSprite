"""Run the real CookSprite Action chain against a remote API/ComfyUI pair."""

from __future__ import annotations

import argparse
import io
import json
import time
from pathlib import Path

import httpx
from PIL import Image


def wait_run(client: httpx.Client, run_id: str, timeout: float = 360) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/v1/runs/{run_id}").raise_for_status().json()
        if run["status"] in {"succeeded", "failed", "cancelled"}:
            if run["status"] != "succeeded":
                raise AssertionError(json.dumps(run, ensure_ascii=False, indent=2))
            return run
        time.sleep(0.25)
    raise TimeoutError(run_id)


def model_for(client: httpx.Client, action_id: str) -> str:
    action = client.get(f"/api/v1/actions/{action_id}").raise_for_status().json()
    assert action["available"] and action["models"], action
    return action["models"][0]["id"]


def run(base_url: str, output: Path) -> None:
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30) as client:
        health = client.get("/api/v1/health").raise_for_status().json()
        assert health["runtime"] == "ready", health
        project = (
            client.post(
                "/api/v1/projects", json={"name": "Remote runtime acceptance", "type": "character"}
            )
            .raise_for_status()
            .json()
        )

        image_run = (
            client.post(
                "/api/v1/actions/image.generate/runs",
                json={
                    "project": project["id"],
                    "inputs": {},
                    "values": {
                        "model": model_for(client, "image.generate"),
                        "prompt": "single tiny soup alchemist game sprite, full body",
                        "category": "character",
                        "style": "pixel",
                        "count": 1,
                        "seed": 240814,
                    },
                },
            )
            .raise_for_status()
            .json()
        )
        image_run = wait_run(client, image_run["id"])
        assert len(image_run["artifacts"]) == 1
        character = image_run["artifacts"][0]
        content = client.get(character["url"]).raise_for_status().content
        with Image.open(io.BytesIO(content)) as image:
            assert image.format == "PNG"
            assert image.mode == "RGBA"
            alpha = image.getchannel("A")
            alpha_min, alpha_max = alpha.getextrema()

        animation_run = (
            client.post(
                "/api/v1/actions/animation.generate/runs",
                json={
                    "project": project["id"],
                    "inputs": {"character": character["id"]},
                    "values": {
                        "model": model_for(client, "animation.generate"),
                        "prompt": "short clear walk cycle",
                        "action": "walk",
                        "view": "level",
                        "direction": "s",
                        "count": 2,
                        "seed": 240815,
                    },
                },
            )
            .raise_for_status()
            .json()
        )
        animation_run = wait_run(client, animation_run["id"])
        assert len(animation_run["artifacts"]) == 1
        sequence = animation_run["artifacts"][0]
        assert sequence["kind"] == "FrameSeq"
        expanded = (
            client.get(f"/api/v1/artifacts/{sequence['id']}/sequence").raise_for_status().json()
        )
        assert len(expanded["frames"]) == 2
        assert expanded["sequence"]["schema"] == "cooksprite.frame-sequence/v1"
        assert expanded["sequence"]["action"] == "walk"
        assert expanded["sequence"]["view"] == "level"
        assert expanded["sequence"]["direction"] == "s"

        normal_run = (
            client.post(
                "/api/v1/actions/normal.generate/runs",
                json={
                    "project": project["id"],
                    "inputs": {"source": sequence["id"]},
                    "values": {
                        "model": model_for(client, "normal.generate"),
                        "strength": 1,
                        "flip_y": False,
                    },
                },
            )
            .raise_for_status()
            .json()
        )
        normal_run = wait_run(client, normal_run["id"])
        expected_sources = [item["id"] for item in expanded["frames"]]
        actual_sources = [item["meta"]["source_artifacts"][0] for item in normal_run["artifacts"]]
        assert actual_sources == expected_sources

        result = {
            "schema": "cooksprite.remote-real-acceptance/v1",
            "base_url": base_url,
            "runtime": health,
            "project_id": project["id"],
            "image_run": image_run["id"],
            "image_artifact": character["id"],
            "image_alpha_range": [alpha_min, alpha_max],
            "animation_run": animation_run["id"],
            "frame_sequence": sequence["id"],
            "frames": expected_sources,
            "normal_run": normal_run["id"],
            "normals": [item["id"] for item in normal_run["artifacts"]],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.api, arguments.output)
