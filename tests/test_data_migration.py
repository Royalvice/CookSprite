from __future__ import annotations

import json
from pathlib import Path

import pytest

import cooksprite.data_migration as migration
from cooksprite.store import RUN_HISTORY_LIMIT, SCHEMA_VERSION, Store


def _v6_fixture(root: Path) -> tuple[str, str]:
    store = Store(root)
    project = store.create_project("Migration", "static")
    artifact = store.put_artifact(
        b"verified-blob",
        "application/octet-stream",
        "Image",
        project_id=project.id,
    )
    document = store.document(project.id)
    assert document
    body = {**document["document"], "history": [{"operation": "legacy"}]}
    store.db.execute(
        "UPDATE project_documents SET body=? WHERE project_id=?",
        (json.dumps(body), project.id),
    )
    for index in range(RUN_HISTORY_LIMIT + 7):
        store.db.execute(
            "INSERT INTO runs(id,status,progress,message,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (
                f"run_terminal_{index:03d}",
                "succeeded",
                1.0,
                "done",
                f"2026-01-01T00:00:{index:03d}Z",
                f"2026-01-01T00:00:{index:03d}Z",
            ),
        )
    store.db.execute(
        "INSERT INTO runs(id,status,progress,message,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        ("run_active", "running", 0.5, "running", "2026-02-01", "2026-02-01"),
    )
    for revision in range(1, 6):
        store.save_definition(
            "workflow",
            "workflow.fixture",
            "runtime.fixture",
            "snapshot.fixture",
            {"revision_payload": revision},
        )
    (root / ".bridge-secret").write_text("fixture-secret\n", encoding="utf-8")
    store.db.execute("PRAGMA user_version=6")
    store.db.commit()
    store.db.close()
    return project.id, artifact.id


def test_verified_v6_data_migration_applies_bounded_current_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source-data"
    target = tmp_path / "target-data"
    project_id, artifact_id = _v6_fixture(source)
    monkeypatch.setattr(migration, "_database_in_use", lambda _path: False)

    result = migration.migrate_data_dir(source, target)

    assert result["source_preserved"] is True
    assert source.is_dir()
    assert result["target"]["schema_version"] == SCHEMA_VERSION
    assert result["target"]["integrity"] == "ok"
    assert result["target"]["verified_blobs"] == 1
    assert (target / ".bridge-secret").read_text(encoding="utf-8") == "fixture-secret\n"
    migrated = Store(target)
    assert migrated.project(project_id)
    assert migrated.artifact(artifact_id)
    assert "history" not in migrated.document(project_id)["document"]
    statuses = migrated.db.execute(
        "SELECT status,COUNT(*) AS count FROM runs GROUP BY status"
    ).fetchall()
    assert {row["status"]: row["count"] for row in statuses} == {
        "running": 1,
        "succeeded": RUN_HISTORY_LIMIT,
    }
    assert migrated.db.execute("SELECT COUNT(*) FROM definitions").fetchone()[0] == 1
    migrated.db.close()


def test_data_migration_rejects_corrupted_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source-data"
    _project_id, artifact_id = _v6_fixture(source)
    store = Store(source)
    artifact = store.artifact(artifact_id)
    store.db.close()
    (source / "artifacts" / artifact["sha256"]).write_bytes(b"corrupt")
    monkeypatch.setattr(migration, "_database_in_use", lambda _path: False)

    with pytest.raises(migration.DataMigrationError, match="Artifact verification failed"):
        migration.migrate_data_dir(source, tmp_path / "target-data")
