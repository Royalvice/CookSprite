from __future__ import annotations

import hashlib
import zipfile

from fastapi.testclient import TestClient
import pytest

from cooksprite.api.app import create_app
from cooksprite.package import build_package
from cooksprite.store import Store


def test_staged_blob_promotion_is_atomic_and_deduplicated(tmp_path) -> None:
    store = Store(tmp_path)
    payload = (b"CookSprite artifact chunk\n" * 65536) + b"tail"
    digest = hashlib.sha256(payload).hexdigest()
    staging = store.new_artifact_upload_path()
    with staging.open("xb") as handle:
        for offset in range(0, len(payload), 4093):
            handle.write(payload[offset : offset + 4093])
        handle.flush()

    first = store.put_artifact_file(staging, digest, len(payload), "application/octet-stream")
    assert store.artifact_path(first.id).read_bytes() == payload
    assert not staging.exists()

    duplicate = store.new_artifact_upload_path()
    duplicate.write_bytes(payload)
    second = store.put_artifact_file(duplicate, digest, len(payload), "application/octet-stream")
    assert second.id == first.id
    assert not duplicate.exists()

    # Content-addressed metadata may survive an interrupted/manual blob loss.
    # A duplicate upload repairs the missing or corrupted entity atomically.
    store.artifact_path(first.id).write_bytes(b"corrupted")
    repair = store.new_artifact_upload_path()
    repair.write_bytes(payload)
    repaired = store.put_artifact_file(repair, digest, len(payload), "application/octet-stream")
    assert repaired.id == first.id
    assert store.artifact_path(first.id).read_bytes() == payload
    assert not repair.exists()


def test_staging_validation_cleans_only_store_owned_files(tmp_path) -> None:
    store = Store(tmp_path)
    payload = b"artifact"
    staging = store.new_artifact_upload_path()
    staging.write_bytes(payload)
    with pytest.raises(ValueError, match="unexpected size"):
        store.put_artifact_file(
            staging, hashlib.sha256(payload).hexdigest(), len(payload) + 1, "application/octet-stream"
        )
    assert not staging.exists()

    outside = tmp_path / "user-file.bin"
    outside.write_bytes(payload)
    with pytest.raises(ValueError, match="inside the Blob Store"):
        store.put_artifact_file(
            outside, hashlib.sha256(payload).hexdigest(), len(payload), "application/octet-stream"
        )
    assert outside.read_bytes() == payload


def test_gc_never_removes_an_inflight_artifact_staging_file(tmp_path) -> None:
    store = Store(tmp_path)
    staging = store.new_artifact_upload_path()
    staging.write_bytes(b"still-uploading")
    orphan = store.blobs / ("a" * 64)
    orphan.write_bytes(b"orphan")

    assert store.gc() == 1
    assert staging.read_bytes() == b"still-uploading"
    assert not orphan.exists()


def test_package_export_streams_blobs_into_one_staged_artifact(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path)
    project = store.create_project("Streamed package", "static")
    diffuse = store.put_artifact(
        b"diffuse-bytes" * 100_000,
        "image/png",
        project_id=project.id,
    )
    normal = store.put_artifact(
        b"normal-bytes" * 100_000,
        "image/png",
        kind="NormalMap",
        project_id=project.id,
    )
    current = store.document(project.id)
    assert current is not None
    document = current["document"]
    document["static"]["primary"] = diffuse.id
    document["static"]["normal"] = normal.id
    store.put_document(project.id, document, current["etag"])

    monkeypatch.setattr(
        store,
        "artifact_bytes",
        lambda _artifact_id: pytest.fail("package export must stream artifact files"),
    )
    result = build_package(store, project.id)

    assert result.staging_path.is_file()
    assert result.size == result.staging_path.stat().st_size
    with zipfile.ZipFile(result.staging_path) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "provenance.json",
            "frames/primary.png",
            "normals/primary.png",
        }
        assert archive.read("frames/primary.png") == b"diffuse-bytes" * 100_000
        assert archive.read("normals/primary.png") == b"normal-bytes" * 100_000
    package = store.put_artifact_file(
        result.staging_path,
        result.sha256,
        result.size,
        "application/vnd.cooksprite+zip",
        "CookSpritePack",
        project_id=project.id,
    )
    assert not result.staging_path.exists()
    assert store.artifact_path(package.id).is_file()


def test_project_metadata_never_materializes_a_second_artifact_tree(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))
    project = client.post("/api/v1/projects", json={"name": "Only Metadata"}).json()
    assert "directory" not in project
    assert not (tmp_path / "artifacts" / "projects").exists()

    payload = b"streamed-artifact" * 1024
    artifact = client.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "media_type": "application/octet-stream"},
        content=payload,
    ).json()
    assert client.get(artifact["url"]).content == payload
    assert not (tmp_path / "artifacts" / "projects").exists()
    assert client.get(f"/api/v1/projects/{project['id']}/artifacts").json()[0]["id"] == artifact["id"]


def test_raw_artifact_upload_limit_rejects_before_leaving_a_staging_file(tmp_path) -> None:
    client = TestClient(create_app(tmp_path, max_artifact_upload_bytes=8))
    response = client.post(
        "/api/v1/artifacts",
        params={"media_type": "application/octet-stream"},
        content=b"123456789",
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "artifact_too_large"
    assert not list((tmp_path / "artifacts").glob(".cooksprite-upload-*.tmp"))
