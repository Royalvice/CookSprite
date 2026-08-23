from __future__ import annotations

import time
import urllib.parse

from fastapi.testclient import TestClient

from cooksprite.api.app import create_app
from cooksprite.bridge import ArtifactBridge


def relative(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))


def test_signed_bridge_is_run_scoped_and_persists_typed_outputs(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/runtimes",
            json={
                "id": "rt_bridge",
                "label": "Bridge",
                "base_url": "http://comfy.invalid",
                "location": "local",
            },
        ).status_code
        == 200
    )
    project = client.post("/api/v1/projects", json={"type": "static"}).json()
    source = client.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "Image", "media_type": "image/png"},
        content=b"source-bytes",
    ).json()
    unrelated = client.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "Image", "media_type": "image/png"},
        content=b"unrelated-bytes",
    ).json()
    app.state.store.create_run(
        "run_bridge",
        "rt_bridge",
        action_id="normal.generate",
        project_id=project["id"],
        request={"inputs": {"source": [source["id"]]}},
    )
    bridge = ArtifactBridge.from_data_dir(tmp_path, "http://api.test/api/v1")

    download = client.get(relative(bridge.download_url(source["id"], "run_bridge")))
    assert download.status_code == 200
    assert download.content == b"source-bytes"

    forbidden = client.get(relative(bridge.download_url(unrelated["id"], "run_bridge")))
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "bridge_scope_violation"

    upload_url = relative(bridge.upload_url("run_bridge", "NormalMap", source["id"]))
    uploaded = client.post(upload_url + "&output_index=0", content=b"normal-bytes")
    assert uploaded.status_code == 200
    assert uploaded.json()["kind"] == "NormalMap"
    assert uploaded.json()["meta"]["source_artifacts"] == [source["id"]]
    assert app.state.store.run("run_bridge")["artifacts"] != "[]"


def test_bridge_rejects_expired_signature_and_has_no_legacy_route(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/runtimes",
        json={
            "id": "rt_bridge",
            "label": "Bridge",
            "base_url": "http://comfy.invalid",
            "location": "local",
        },
    )
    app.state.store.create_run("run_bridge", "rt_bridge", request={"inputs": {}})
    bridge = ArtifactBridge.from_data_dir(tmp_path, "http://api.test/api/v1")
    expires = int(time.time()) - 1
    signature = bridge._signature("upload", "run_bridge", "Image", "", expires)
    expired = client.post(
        f"/api/v1/bridge/runs/run_bridge/artifacts?kind=Image&source_artifact=&expires={expires}&signature={signature}",
        content=b"image",
    )
    assert expired.status_code == 403
    assert "/api/v1/internal/artifacts" not in app.openapi()["paths"]
    assert client.post("/api/v1/internal/artifacts", content=b"image").status_code in {404, 405}
