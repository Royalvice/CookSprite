"""Protocol-faithful ComfyUI stand-in used only by browser/contract tests."""

from __future__ import annotations

import io
import json
import os
import uuid

import httpx
from fastapi import FastAPI
from PIL import Image, ImageDraw

app = FastAPI()
history: dict[str, dict] = {}
interrupted = False
API = os.environ.get("COOKSPRITE_TEST_API", "http://127.0.0.1:8000/api/v1")


@app.get("/object_info")
def object_info():
    return {
        "CS_DemoAction": {
            "input": {
                "required": {
                    "action_id": ["STRING"],
                    "values_json": ["STRING"],
                    "count": ["INT"],
                }
            },
            "output": ["IMAGE"],
        },
        "CS_StoreArtifact": {
            "input": {
                "required": {
                    "value": ["IMAGE"],
                    "kind": ["STRING"],
                    "run_id": ["STRING"],
                }
            },
            "output": ["STRING"],
        },
    }


@app.get("/system_stats")
def system_stats():
    return {"system": {"comfyui_version": "cooksprite-test-runtime", "device": "protocol-stub"}}


@app.post("/prompt")
def prompt(body: dict):
    prompt_id = f"prompt_{uuid.uuid4().hex}"
    graph = body["prompt"]
    demo = next(node for node in graph.values() if node["class_type"] == "CS_DemoAction")
    sinks = [node for node in graph.values() if node["class_type"] == "CS_StoreArtifact"]
    values = json.loads(demo["inputs"]["values_json"])
    count = int(demo["inputs"].get("count", 1))
    for sink in sinks:
        run_id = sink["inputs"]["run_id"]
        kind = sink["inputs"]["kind"]
        source_artifact = sink["inputs"].get("source_artifact")
        for index in range(count):
            image = render_sprite(demo["inputs"]["action_id"], values, index, kind)
            httpx.post(
                f"{API}/internal/artifacts",
                params={
                    "run_id": run_id,
                    "kind": kind,
                    "media_type": "image/png",
                    "source_artifact": source_artifact,
                    "output_index": index,
                },
                content=image,
                timeout=10,
            ).raise_for_status()
    history[prompt_id] = {"status": {"completed": True}, "outputs": {}, "prompt": graph}
    return {"prompt_id": prompt_id, "number": len(history), "node_errors": {}}


@app.get("/history/{prompt_id}")
def prompt_history(prompt_id: str):
    return {prompt_id: history[prompt_id]} if prompt_id in history else {}


@app.get("/queue")
def get_queue():
    return {"queue_running": [], "queue_pending": []}


@app.post("/queue")
def delete_queue(_body: dict):
    return {}


@app.post("/interrupt")
def interrupt():
    global interrupted
    interrupted = True
    return {}


def render_sprite(action_id: str, values: dict, index: int, kind: str) -> bytes:
    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if action_id == "normal.generate" or kind == "NormalMap":
        draw.rectangle((10, 10, 86, 86), fill=(128, 128, 255, 255))
    else:
        # Produce distinct candidates so the browser test exercises multi-output
        # artifact handling instead of content-address deduplication.
        wobble = (index % 4) - 2
        draw.rectangle((29, 10 + wobble, 67, 23 + wobble), fill=(249, 238, 206, 255))
        draw.rectangle((23, 18 + wobble, 73, 29 + wobble), fill=(249, 238, 206, 255))
        draw.rectangle((32, 29 + wobble, 64, 48 + wobble), fill=(225, 156, 111, 255))
        draw.rectangle((23, 48 + wobble, 73, 75 + wobble), fill=(111, 48, 132, 255))
        draw.rectangle((26, 70 + wobble, 39, 89), fill=(46, 35, 55, 255))
        draw.rectangle((57, 70 - wobble, 70, 89), fill=(46, 35, 55, 255))
        accent_red = {
            "image.generate": 25,
            "animation.generate": 237,
            "frame.redraw": 180,
            "sheet.slice": 80,
            "video.sample": 120,
        }.get(action_id, 25)
        draw.rectangle(
            (28, 47 + wobble, 68, 53 + wobble), fill=(accent_red, 170 + (index * 7) % 70, 220, 255)
        )
        draw.ellipse(
            (63, 43 + wobble, 89, 67 + wobble),
            fill=(46, 48, 58, 255),
            outline=(237, 176, 55, 255),
            width=3,
        )
        draw.rectangle((39, 34 + wobble, 44, 39 + wobble), fill=(35, 24, 31, 255))
        draw.rectangle((53, 34 + wobble, 58, 39 + wobble), fill=(35, 24, 31, 255))
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()
