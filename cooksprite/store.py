"""SQLite metadata and SHA-256 content-addressed artifact storage."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import ArtifactRef, ProjectView, RunRuntimeState, SpriteDocument

SCHEMA_VERSION = 6
PROJECT_ID_PATTERN = r"prj_[\w-]{1,96}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentConflict(RuntimeError):
    pass


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs = self.root / "artifacts"
        self.blobs.mkdir(exist_ok=True)
        self.project_roots = self.blobs / "projects"
        self.project_roots.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "cooksprite.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self.lock:
            previous_version = self.db.execute("PRAGMA user_version").fetchone()[0]
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtimes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    snapshot TEXT,
                    tools TEXT NOT NULL DEFAULT '[]',
                    assets TEXT NOT NULL DEFAULT '[]',
                    location TEXT NOT NULL DEFAULT 'remote',
                    transport TEXT NOT NULL DEFAULT 'http',
                    directory TEXT
                );
                CREATE TABLE IF NOT EXISTS definitions (
                    kind TEXT NOT NULL,
                    id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    runtime_id TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY(kind,id,revision)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    sha256 TEXT UNIQUE NOT NULL,
                    media_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    meta TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    message TEXT NOT NULL,
                    error TEXT,
                    artifacts TEXT NOT NULL DEFAULT '[]',
                    runtime_id TEXT,
                    prompt_id TEXT
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    published INTEGER NOT NULL DEFAULT 0,
                    cover_artifact_id TEXT,
                    published_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_documents (
                    project_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    etag TEXT NOT NULL,
                    body TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE TABLE IF NOT EXISTS project_artifacts (
                    project_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'asset',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, artifact_id),
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._ensure_column("artifacts", "title", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("artifacts", "favorite", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("artifacts", "trashed", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("artifacts", "created_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("runs", "action_id", "TEXT")
            self._ensure_column("runs", "project_id", "TEXT")
            self._ensure_column("runs", "request", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column("runs", "created_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("runs", "updated_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("runs", "provenance", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column("runs", "runtime_state", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column("definitions", "body_hash", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("runtimes", "location", "TEXT NOT NULL DEFAULT 'remote'")
            self._ensure_column("runtimes", "transport", "TEXT NOT NULL DEFAULT 'http'")
            self._ensure_column("runtimes", "directory", "TEXT")
            definition_rows = self.db.execute(
                "SELECT kind,id,revision,body FROM definitions WHERE body_hash=''"
            ).fetchall()
            for row in definition_rows:
                digest = hashlib.sha256(
                    json.dumps(
                        json.loads(row["body"]), sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                self.db.execute(
                    "UPDATE definitions SET body_hash=? WHERE kind=? AND id=? AND revision=?",
                    (digest, row["kind"], row["id"], row["revision"]),
                )
            self.db.commit()
        if previous_version and previous_version < 3:
            self._migrate_legacy_frame_sequences()
        with self.lock:
            self.db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)",
                (SCHEMA_VERSION, utcnow()),
            )
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.db.commit()

    def _migrate_legacy_frame_sequences(self) -> None:
        """Turn v2 image-shaped FrameSeq rows into Images plus one typed manifest per run."""
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM artifacts WHERE kind='FrameSeq' AND media_type LIKE 'image/%' "
                "ORDER BY created_at,id"
            ).fetchall()
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            meta = json.loads(item.get("meta") or "{}")
            groups.setdefault(str(meta.get("run_id") or f"legacy:{item['id']}"), []).append(item)
        for group_id, frames in groups.items():
            run = self.run(group_id) if not group_id.startswith("legacy:") else None
            request = json.loads(run.get("request") or "{}") if run else {}
            values = request.get("values", {})
            action = values.get("action") if isinstance(values.get("action"), str) else None
            raw_view = values.get("view")
            view = (
                raw_view
                if isinstance(raw_view, str)
                else raw_view[0]
                if isinstance(raw_view, list) and len(raw_view) == 1
                else None
            )
            raw_direction = values.get("direction", values.get("directions"))
            direction = (
                raw_direction
                if isinstance(raw_direction, str)
                else raw_direction[0]
                if isinstance(raw_direction, list) and len(raw_direction) == 1
                else None
            )
            frame_ids = [item["id"] for item in frames]
            with self.lock:
                for artifact_id in frame_ids:
                    self.db.execute("UPDATE artifacts SET kind='Image' WHERE id=?", (artifact_id,))
                self.db.commit()
            manifest = {
                "schema": "cooksprite.frame-sequence/v1",
                "action": action,
                "view": view,
                "direction": direction,
                "frames": frame_ids,
            }
            project_id = run.get("project_id") if run else None
            sequence = self.put_artifact(
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode(),
                "application/vnd.cooksprite.frame-sequence+json",
                "FrameSeq",
                {
                    "role": "frame_sequence",
                    "run_id": group_id if run else None,
                    "action_id": run.get("action_id") if run else None,
                    "frame_count": len(frame_ids),
                    "needs_target": not all((action, view, direction)),
                },
                project_id=project_id,
                title=f"{action or 'Imported'} sequence",
            )
            if run:
                self.set_run_artifacts(group_id, [sequence.id])

    def _ensure_column(self, table: str, name: str, declaration: str) -> None:
        columns = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def runtime(self, runtime_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM runtimes WHERE id=?", (runtime_id,)).fetchone()
        return dict(row) if row else None

    def runtimes(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM runtimes ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def active_runtime(self, runtime_id: str | None = None) -> dict[str, Any] | None:
        if runtime_id:
            runtime = self.runtime(runtime_id)
            return runtime
        with self.lock:
            selected = self.db.execute(
                "SELECT value FROM settings WHERE key='active_runtime_id'"
            ).fetchone()
            if selected:
                row = self.db.execute(
                    "SELECT * FROM runtimes WHERE id=?", (selected["value"],)
                ).fetchone()
                if row:
                    return dict(row)
            row = self.db.execute(
                "SELECT * FROM runtimes ORDER BY CASE WHEN snapshot IS NOT NULL AND snapshot != '' "
                "THEN 0 ELSE 1 END, id LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def set_active_runtime(self, runtime_id: str) -> None:
        with self.lock:
            exists = self.db.execute("SELECT 1 FROM runtimes WHERE id=?", (runtime_id,)).fetchone()
            if not exists:
                raise FileNotFoundError(runtime_id)
            self.db.execute(
                "INSERT INTO settings(key,value) VALUES('active_runtime_id',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (runtime_id,),
            )
            self.db.commit()

    def active_runtime_id(self) -> str | None:
        with self.lock:
            row = self.db.execute(
                "SELECT value FROM settings WHERE key='active_runtime_id'"
            ).fetchone()
        return str(row["value"]) if row else None

    def delete_runtime(self, runtime_id: str) -> str | None:
        """Remove one saved connection without touching its process or artifacts."""

        with self.lock:
            row = self.db.execute("SELECT id FROM runtimes WHERE id=?", (runtime_id,)).fetchone()
            if not row:
                raise FileNotFoundError(runtime_id)
            running = self.db.execute(
                "SELECT COUNT(*) AS count FROM runs "
                "WHERE runtime_id=? AND status IN ('queued','running','cancel_requested')",
                (runtime_id,),
            ).fetchone()["count"]
            if running:
                raise RuntimeError(f"runtime {runtime_id} has {running} active run(s)")
            active = self.db.execute(
                "SELECT value FROM settings WHERE key='active_runtime_id'"
            ).fetchone()
            active_id = str(active["value"]) if active else None
            self.db.execute("DELETE FROM runtimes WHERE id=?", (runtime_id,))
            next_id: str | None = active_id
            if active_id == runtime_id:
                next_row = self.db.execute(
                    "SELECT id FROM runtimes ORDER BY CASE WHEN snapshot IS NOT NULL "
                    "AND snapshot != '' THEN 0 ELSE 1 END, id LIMIT 1"
                ).fetchone()
                next_id = str(next_row["id"]) if next_row else None
                if next_id:
                    self.db.execute(
                        "INSERT INTO settings(key,value) VALUES('active_runtime_id',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (next_id,),
                    )
                else:
                    self.db.execute("DELETE FROM settings WHERE key='active_runtime_id'")
            self.db.commit()
            return next_id

    def active_run_count(self, runtime_id: str) -> int:
        with self.lock:
            row = self.db.execute(
                "SELECT COUNT(*) AS count FROM runs "
                "WHERE runtime_id=? AND status IN ('queued','running','cancel_requested')",
                (runtime_id,),
            ).fetchone()
        return int(row["count"])

    def put_runtime(
        self,
        runtime_id: str,
        label: str,
        base_url: str,
        snapshot: str = "",
        tools: list | None = None,
        assets: list | None = None,
        location: str = "remote",
        transport: str = "http",
        directory: str | None = None,
    ) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO runtimes "
                "(id,label,base_url,snapshot,tools,assets,location,transport,directory) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    runtime_id,
                    label,
                    base_url,
                    snapshot,
                    json.dumps(tools or []),
                    json.dumps(assets or []),
                    location,
                    transport,
                    directory,
                ),
            )
            self.db.commit()

    def save_definition(
        self, kind: str, definition_id: str, runtime_id: str, snapshot: str, body: dict
    ) -> int:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        body_hash = hashlib.sha256(canonical.encode()).hexdigest()
        with self.lock:
            existing = self.db.execute(
                "SELECT revision FROM definitions WHERE kind=? AND id=? AND runtime_id=? "
                "AND snapshot=? AND body_hash=? ORDER BY revision DESC LIMIT 1",
                (kind, definition_id, runtime_id, snapshot, body_hash),
            ).fetchone()
            if existing:
                return int(existing["revision"])
            row = self.db.execute(
                "SELECT MAX(revision) AS revision FROM definitions WHERE kind=? AND id=?",
                (kind, definition_id),
            ).fetchone()
            revision = (row["revision"] or 0) + 1
            self.db.execute(
                "INSERT INTO definitions(kind,id,revision,runtime_id,snapshot,body,body_hash) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    kind,
                    definition_id,
                    revision,
                    runtime_id,
                    snapshot,
                    canonical,
                    body_hash,
                ),
            )
            self.db.commit()
        return revision

    def definition(self, kind: str, definition_id: str, revision: int | None = None) -> dict | None:
        query = "SELECT * FROM definitions WHERE kind=? AND id=?"
        values: list[Any] = [kind, definition_id]
        if revision is None:
            query += " ORDER BY revision DESC LIMIT 1"
        else:
            query += " AND revision=?"
            values.append(revision)
        with self.lock:
            row = self.db.execute(query, values).fetchone()
        return dict(row) if row else None

    def definitions(self, kind: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM definitions WHERE kind=? ORDER BY id, revision DESC", (kind,)
            ).fetchall()
        return [dict(row) for row in rows]

    def create_project(self, name: str, project_type: str) -> ProjectView:
        now = utcnow()
        clean_name = name.strip() or "Untitled Sprite"
        prefix = self._project_name_prefix(clean_name)
        document = SpriteDocument(type=project_type).model_dump(mode="json", by_alias=True)
        body = json.dumps(document, sort_keys=True, separators=(",", ":"))
        etag = self._etag(1, body)
        with self.lock:
            for _ in range(16):
                project_id = f"prj_{prefix}_{uuid.uuid4().hex[:8]}"
                if not self.db.execute(
                    "SELECT 1 FROM projects WHERE id=?", (project_id,)
                ).fetchone():
                    break
            else:
                raise RuntimeError("could not allocate a unique project id")
            self.db.execute(
                "INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?)",
                (project_id, clean_name, project_type, 0, 0, None, None, now, now),
            )
            self.db.execute(
                "INSERT INTO project_documents VALUES(?,?,?,?,?)",
                (project_id, 1, etag, body, now),
            )
            self.db.commit()
        self.materialize_project(project_id)
        return self.project(project_id)  # type: ignore[return-value]

    @staticmethod
    def _project_name_prefix(name: str) -> str:
        prefix = "".join(char if char.isalnum() or char in "_-" else "_" for char in name)
        prefix = re.sub(r"_+", "_", prefix).strip("_-").lower()
        return (prefix[:48].rstrip("_-") or "untitled_sprite")

    def project(self, project_id: str) -> ProjectView | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._project_view(row) if row else None

    def projects(self) -> list[ProjectView]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [self._project_view(row) for row in rows]

    def patch_project(self, project_id: str, fields: dict[str, Any]) -> ProjectView | None:
        allowed = {"name", "type", "favorite"}
        changes = {
            key: value for key, value in fields.items() if key in allowed and value is not None
        }
        if not changes:
            return self.project(project_id)
        changes["updated_at"] = utcnow()
        keys = ",".join(f"{key}=?" for key in changes)
        with self.lock:
            self.db.execute(
                f"UPDATE projects SET {keys} WHERE id=?", [*changes.values(), project_id]
            )
            self.db.commit()
        self.materialize_project(project_id)
        return self.project(project_id)

    def publish_project(self, project_id: str, cover_artifact_id: str | None) -> ProjectView | None:
        now = utcnow()
        with self.lock:
            self.db.execute(
                "UPDATE projects SET published=1,cover_artifact_id=?,published_at=?,updated_at=? WHERE id=?",
                (cover_artifact_id, now, now, project_id),
            )
            self.db.commit()
        return self.project(project_id)

    def gallery(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM projects WHERE published=1 ORDER BY published_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            cover = self.artifact(row["cover_artifact_id"]) if row["cover_artifact_id"] else None
            result.append(
                {
                    "project": self._project_view(row),
                    "cover": self.artifact_ref(cover) if cover else None,
                    "published_at": row["published_at"],
                }
            )
        return result

    def _project_view(self, row: sqlite3.Row | dict[str, Any]) -> ProjectView:
        item = dict(row)
        return ProjectView(
            id=item["id"],
            name=item["name"],
            type=item["type"],
            directory=str(self._ensure_project_directory(item["id"])),
            favorite=bool(item["favorite"]),
            published=bool(item["published"]),
            cover_artifact_id=item["cover_artifact_id"],
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        )

    def document(self, project_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM project_documents WHERE project_id=?", (project_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "document": json.loads(row["body"]),
            "revision": row["revision"],
            "etag": row["etag"],
        }

    def put_document(
        self, project_id: str, document: dict[str, Any], if_match: str | None
    ) -> dict[str, Any]:
        body = json.dumps(document, sort_keys=True, separators=(",", ":"))
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM project_documents WHERE project_id=?", (project_id,)
            ).fetchone()
            if not row:
                raise FileNotFoundError(project_id)
            if not if_match or if_match.strip('"') != row["etag"]:
                raise DocumentConflict("document changed since it was loaded")
            revision = row["revision"] + 1
            etag = self._etag(revision, body)
            now = utcnow()
            self.db.execute(
                "UPDATE project_documents SET revision=?,etag=?,body=?,updated_at=? WHERE project_id=?",
                (revision, etag, body, now, project_id),
            )
            self.db.execute(
                "UPDATE projects SET type=?,updated_at=? WHERE id=?",
                (document["type"], now, project_id),
            )
            self.db.commit()
        self.materialize_project(project_id)
        return {"document": document, "revision": revision, "etag": etag}

    @staticmethod
    def _etag(revision: int, body: str) -> str:
        return hashlib.sha256(f"{revision}:{body}".encode()).hexdigest()

    def put_artifact(
        self,
        data: bytes,
        media_type: str,
        kind: str = "Image",
        meta: dict | None = None,
        project_id: str | None = None,
        title: str = "",
    ) -> ArtifactRef:
        sha256 = hashlib.sha256(data).hexdigest()
        now = utcnow()
        with self.lock:
            row = self.db.execute("SELECT * FROM artifacts WHERE sha256=?", (sha256,)).fetchone()
            if not row:
                (self.blobs / sha256).write_bytes(data)
                artifact_id = f"art_{uuid.uuid4().hex[:20]}"
                self.db.execute(
                    "INSERT INTO artifacts(id,sha256,media_type,size,kind,meta,title,favorite,trashed,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        artifact_id,
                        sha256,
                        media_type,
                        len(data),
                        kind,
                        json.dumps(meta or {}),
                        title,
                        0,
                        0,
                        now,
                    ),
                )
                row = self.db.execute(
                    "SELECT * FROM artifacts WHERE id=?", (artifact_id,)
                ).fetchone()
            if project_id:
                self.db.execute(
                    "INSERT OR IGNORE INTO project_artifacts VALUES(?,?,?,?)",
                    (project_id, row["id"], (meta or {}).get("role", "asset"), now),
                )
                self.db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
            self.db.commit()
        if project_id:
            self.materialize_project(project_id)
        return self.artifact_ref(row, project_id)

    def link_artifact(self, project_id: str, artifact_id: str, role: str = "asset") -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR IGNORE INTO project_artifacts VALUES(?,?,?,?)",
                (project_id, artifact_id, role, utcnow()),
            )
            self.db.commit()
        self.materialize_project(project_id)

    def artifact_ref(
        self, row: sqlite3.Row | dict[str, Any], project_id: str | None = None
    ) -> ArtifactRef:
        item = dict(row)
        return ArtifactRef(
            id=item["id"],
            sha256=item["sha256"],
            media_type=item["media_type"],
            size=item["size"],
            kind=item["kind"],
            url=f"/api/v1/artifacts/{item['id']}/content",
            title=item.get("title", ""),
            project_id=project_id,
            favorite=bool(item.get("favorite", 0)),
            trashed=bool(item.get("trashed", 0)),
            meta=json.loads(item.get("meta") or "{}"),
            created_at=item.get("created_at", ""),
        )

    def artifact(self, artifact_id: str | None) -> dict[str, Any] | None:
        if not artifact_id:
            return None
        with self.lock:
            row = self.db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return dict(row) if row else None

    def patch_artifact(self, artifact_id: str, patch: dict[str, Any]) -> ArtifactRef | None:
        allowed = {key: value for key, value in patch.items() if key in {"favorite", "title"}}
        if not allowed:
            row = self.artifact(artifact_id)
            return self.artifact_ref(row) if row else None
        if "favorite" in allowed:
            allowed["favorite"] = int(bool(allowed["favorite"]))
        with self.lock:
            exists = self.db.execute(
                "SELECT 1 FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
            if not exists:
                return None
            assignments = ",".join(f"{key}=?" for key in allowed)
            self.db.execute(
                f"UPDATE artifacts SET {assignments} WHERE id=?",
                (*allowed.values(), artifact_id),
            )
            self.db.commit()
            row = self.db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return self.artifact_ref(row)

    def artifacts(
        self,
        project_id: str | None = None,
        kind: str | None = None,
        trashed: bool = False,
        search: str = "",
    ) -> list[ArtifactRef]:
        values: list[Any] = []
        if project_id:
            query = (
                "SELECT a.* FROM artifacts a JOIN project_artifacts pa ON pa.artifact_id=a.id "
                "WHERE pa.project_id=?"
            )
            values.append(project_id)
        else:
            query = "SELECT a.* FROM artifacts a WHERE 1=1"
        query += " AND a.trashed=?"
        values.append(1 if trashed else 0)
        if kind:
            query += " AND a.kind=?"
            values.append(kind)
        if search:
            query += " AND (a.title LIKE ? OR a.meta LIKE ?)"
            values.extend([f"%{search}%", f"%{search}%"])
        query += " ORDER BY a.created_at DESC,a.id DESC"
        with self.lock:
            rows = self.db.execute(query, values).fetchall()
        return [self.artifact_ref(row, project_id) for row in rows]

    def project_artifacts(self, project_id: str) -> list[ArtifactRef]:
        items = self.artifacts(project_id=project_id)
        self.materialize_project(project_id)
        return items

    def _ensure_project_directory(self, project_id: str) -> Path:
        """Create the user-visible project workspace under the API artifact root."""

        if not re.fullmatch(PROJECT_ID_PATTERN, project_id):
            raise ValueError(f"invalid project id: {project_id}")
        directory = self.project_roots / project_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def project_directory(self, project_id: str) -> Path:
        """Materialize a project and return its local, user-visible directory."""

        with self.lock:
            exists = self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
            if not exists:
                raise FileNotFoundError(project_id)
            directory = self._ensure_project_directory(project_id)
            rows = self.db.execute(
                "SELECT a.* FROM artifacts a JOIN project_artifacts pa ON pa.artifact_id=a.id "
                "WHERE pa.project_id=? ORDER BY a.created_at ASC,a.id ASC",
                (project_id,),
            ).fetchall()
            self._write_project_manifest(project_id, directory, rows)
            return directory

    def materialize_project(self, project_id: str) -> Path:
        """Refresh the user-visible project workspace from canonical store data."""

        return self.project_directory(project_id)

    def _artifact_extension(self, row: sqlite3.Row | dict[str, Any]) -> str:
        item = dict(row)
        kind = str(item.get("kind") or "").lower()
        media_type = str(item.get("media_type") or "").lower()
        if kind == "cookspritepack":
            return ".cooksprite"
        if kind == "frameseq" or media_type.endswith("+json") or media_type == "application/json":
            return ".json"
        extension = mimetypes.guess_extension(media_type) or ".bin"
        return ".jpeg" if extension == ".jpe" else extension

    def _copy_artifact_to_project(self, directory: Path, row: sqlite3.Row | dict[str, Any]) -> str:
        item = dict(row)
        filename = f"{item['id']}{self._artifact_extension(item)}"
        target = directory / filename
        source = self.blobs / item["sha256"]
        if source.is_file() and (
            not target.is_file() or target.stat().st_size != source.stat().st_size
        ):
            temporary = target.with_name(f".{filename}.tmp")
            shutil.copyfile(source, temporary)
            temporary.replace(target)
        return filename

    def _write_project_manifest(
        self,
        project_id: str,
        directory: Path,
        rows: list[sqlite3.Row] | None = None,
    ) -> None:
        project_row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        document_row = self.db.execute(
            "SELECT body,revision,etag FROM project_documents WHERE project_id=?", (project_id,)
        ).fetchone()
        if not project_row or not document_row:
            return
        artifact_rows = rows or self.db.execute(
            "SELECT a.* FROM artifacts a JOIN project_artifacts pa ON pa.artifact_id=a.id "
            "WHERE pa.project_id=? ORDER BY a.created_at ASC,a.id ASC",
            (project_id,),
        ).fetchall()
        artifacts = []
        for row in artifact_rows:
            item = dict(row)
            artifacts.append(
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "title": item.get("title", ""),
                    "file": self._copy_artifact_to_project(directory, row),
                    "trashed": bool(item.get("trashed", 0)),
                }
            )
        payload = {
            "schema": "cooksprite.project-workspace/v1",
            "project": {
                "id": project_row["id"],
                "name": project_row["name"],
                "type": project_row["type"],
                "created_at": project_row["created_at"],
                "updated_at": project_row["updated_at"],
            },
            "document": json.loads(document_row["body"]),
            "document_revision": document_row["revision"],
            "document_etag": document_row["etag"],
            "artifacts": artifacts,
        }
        (directory / "project.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def artifact_bytes(self, artifact_id: str) -> bytes:
        row = self.artifact(artifact_id)
        if not row:
            raise FileNotFoundError(artifact_id)
        return (self.blobs / row["sha256"]).read_bytes()

    def set_artifact_trashed(self, artifact_id: str, trashed: bool) -> ArtifactRef | None:
        with self.lock:
            self.db.execute(
                "UPDATE artifacts SET trashed=? WHERE id=?", (int(trashed), artifact_id)
            )
            self.db.commit()
        row = self.artifact(artifact_id)
        return self.artifact_ref(row) if row else None

    def create_run(
        self,
        run_id: str,
        runtime_id: str | None,
        action_id: str | None = None,
        project_id: str | None = None,
        request: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        now = utcnow()
        with self.lock:
            self.db.execute(
                "INSERT INTO runs(id,status,progress,message,error,artifacts,runtime_id,prompt_id,"
                "action_id,project_id,request,created_at,updated_at,provenance,runtime_state) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    "queued",
                    0,
                    "queued",
                    None,
                    "[]",
                    runtime_id,
                    None,
                    action_id,
                    project_id,
                    json.dumps(request or {}),
                    now,
                    now,
                    json.dumps(provenance or {}),
                    RunRuntimeState().model_dump_json(),
                ),
            )
            self.db.commit()

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "progress",
            "message",
            "error",
            "artifacts",
            "runtime_id",
            "prompt_id",
            "provenance",
            "runtime_state",
        }
        changes = {key: value for key, value in fields.items() if key in allowed}
        if "provenance" in changes and not isinstance(changes["provenance"], str):
            changes["provenance"] = json.dumps(changes["provenance"])
        if "runtime_state" in changes and not isinstance(changes["runtime_state"], str):
            changes["runtime_state"] = json.dumps(changes["runtime_state"])
        changes["updated_at"] = utcnow()
        keys = ",".join(f"{key}=?" for key in changes)
        with self.lock:
            self.db.execute(f"UPDATE runs SET {keys} WHERE id=?", [*changes.values(), run_id])
            self.db.commit()

    def attach_run_artifact(
        self, run_id: str, artifact_id: str, *, allow_duplicate: bool = False
    ) -> None:
        with self.lock:
            row = self.db.execute("SELECT artifacts FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return
            artifact_ids = json.loads(row["artifacts"])
            if allow_duplicate or artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
            self.db.execute(
                "UPDATE runs SET artifacts=?,updated_at=? WHERE id=?",
                (json.dumps(artifact_ids), utcnow(), run_id),
            )
            self.db.commit()

    def set_run_artifacts(self, run_id: str, artifact_ids: list[str]) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE runs SET artifacts=?,updated_at=? WHERE id=?",
                (json.dumps(list(dict.fromkeys(artifact_ids))), utcnow(), run_id),
            )
            self.db.commit()

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def runs(self, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM runs"
        values: list[Any] = []
        if statuses:
            query += f" WHERE status IN ({','.join('?' for _ in statuses)})"
            values.extend(statuses)
        query += " ORDER BY created_at DESC,id DESC"
        with self.lock:
            rows = self.db.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def gc(self) -> int:
        with self.lock:
            known = {row["sha256"] for row in self.db.execute("SELECT sha256 FROM artifacts")}
        removed = 0
        for path in self.blobs.iterdir():
            if path.is_file() and path.name not in known:
                path.unlink()
                removed += 1
        return removed
