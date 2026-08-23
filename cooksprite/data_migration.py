"""Verified relocation of one CookSprite API data directory."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .store import Store


class DataMigrationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_in_use(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["lsof", "-t", "--", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DataMigrationError(
            "cannot prove that the source API is stopped because lsof is unavailable"
        ) from exc
    return bool(result.stdout.strip())


def verify_data_dir(root: str | Path) -> dict[str, Any]:
    directory = Path(root).expanduser().resolve()
    database = directory / "cooksprite.sqlite3"
    if not database.is_file():
        raise DataMigrationError(f"CookSprite database is missing: {database}")
    try:
        db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise DataMigrationError(f"SQLite integrity check failed: {integrity}")
        tables = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        counts = {
            table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "projects",
                "project_documents",
                "project_artifacts",
                "artifacts",
                "runs",
                "runtimes",
                "definitions",
            )
            if table in tables
        }
        artifacts = (
            db.execute("SELECT sha256,size FROM artifacts ORDER BY sha256").fetchall()
            if "artifacts" in tables
            else []
        )
        schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise DataMigrationError(f"could not verify {database}: {exc}") from exc
    finally:
        if "db" in locals():
            db.close()

    blob_root = directory / "artifacts"
    invalid: list[str] = []
    for sha256, size in artifacts:
        path = blob_root / str(sha256)
        if not path.is_file():
            invalid.append(f"missing:{sha256}")
        elif path.stat().st_size != int(size):
            invalid.append(f"size:{sha256}")
        elif _sha256(path) != sha256:
            invalid.append(f"sha256:{sha256}")
    if invalid:
        preview = ", ".join(invalid[:8])
        raise DataMigrationError(
            f"Artifact verification failed for {len(invalid)} blob(s): {preview}"
        )
    return {
        "data_dir": str(directory),
        "schema_version": schema_version,
        "integrity": integrity,
        "counts": counts,
        "verified_blobs": len(artifacts),
    }


def migrate_data_dir(source: str | Path, target: str | Path) -> dict[str, Any]:
    source_dir = Path(source).expanduser().resolve()
    target_dir = Path(target).expanduser().resolve()
    database = source_dir / "cooksprite.sqlite3"
    if source_dir == target_dir:
        raise DataMigrationError("source and target data directories are identical")
    if target_dir.exists():
        raise DataMigrationError(f"target data directory already exists: {target_dir}")
    if _database_in_use(database):
        raise DataMigrationError(
            f"source database is open by a running process: {database}; stop the API first"
        )
    source_report = verify_data_dir(source_dir)
    staging = target_dir.with_name(f".{target_dir.name}.migrate-{uuid.uuid4().hex}")
    try:
        staging.mkdir(parents=True)
        target_db = staging / "cooksprite.sqlite3"
        try:
            source_db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            destination_db = sqlite3.connect(target_db)
            source_db.backup(destination_db)
            destination_db.close()
            source_db.close()
        except sqlite3.Error as exc:
            raise DataMigrationError(f"SQLite backup failed: {exc}") from exc

        source_blobs = source_dir / "artifacts"
        target_blobs = staging / "artifacts"
        target_blobs.mkdir()
        if source_blobs.is_dir():
            for path in source_blobs.iterdir():
                if not path.is_file() or len(path.name) != 64:
                    continue
                destination = target_blobs / path.name
                try:
                    os.link(path, destination)
                except OSError:
                    # Hard links are the cheap same-filesystem path.  A
                    # cross-device or restricted filesystem falls back to a
                    # bounded streaming copy below rather than loading blobs
                    # into API memory.
                    with path.open("rb") as source_handle, destination.open("wb") as target_handle:
                        shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
        secret = source_dir / ".bridge-secret"
        if secret.is_file():
            shutil.copy2(secret, staging / secret.name)

        migrated = Store(staging)
        migrated.db.close()
        target_report = verify_data_dir(staging)
        preserved_tables = (
            "projects",
            "project_documents",
            "project_artifacts",
            "artifacts",
            "runtimes",
        )
        changed = {
            table: (source_report["counts"].get(table), target_report["counts"].get(table))
            for table in preserved_tables
            if source_report["counts"].get(table) != target_report["counts"].get(table)
        }
        if changed:
            raise DataMigrationError(f"preserved table counts differ after migration: {changed}")
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(target_dir)
        return {
            "source": source_report,
            "target": {**target_report, "data_dir": str(target_dir)},
            "source_preserved": True,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


__all__ = [
    "DataMigrationError",
    "migrate_data_dir",
    "verify_data_dir",
]
