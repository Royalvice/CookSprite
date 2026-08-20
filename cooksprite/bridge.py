"""Short-lived, run-scoped URLs used by remote CookSprite ComfyUI nodes."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from urllib.parse import urlencode


class BridgeError(ValueError):
    pass


class ArtifactBridge:
    def __init__(self, secret: bytes, base_url: str, ttl_seconds: int = 3600):
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_data_dir(cls, data_dir: str | Path, base_url: str) -> ArtifactBridge:
        path = Path(data_dir) / ".bridge-secret"
        if path.exists():
            secret = path.read_bytes()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            secret = os.urandom(32)
            path.write_bytes(secret)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return cls(secret, base_url)

    def _signature(self, *parts: object) -> str:
        payload = "\n".join(str(part) for part in parts).encode()
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    def download_url(self, artifact_id: str, run_id: str, *, expand: str = "") -> str:
        expires = int(time.time()) + self.ttl_seconds
        if expand:
            signature = self._signature("download", artifact_id, run_id, expand, expires)
            query = urlencode({"run_id": run_id, "expand": expand, "expires": expires, "signature": signature})
        else:
            signature = self._signature("download", artifact_id, run_id, expires)
            query = urlencode({"run_id": run_id, "expires": expires, "signature": signature})
        return f"{self.base_url}/bridge/artifacts/{artifact_id}?{query}"

    def upload_url(self, run_id: str, kind: str, source_artifact: str = "") -> str:
        expires = int(time.time()) + self.ttl_seconds
        signature = self._signature("upload", run_id, kind, source_artifact, expires)
        query = urlencode(
            {
                "kind": kind,
                "source_artifact": source_artifact,
                "expires": expires,
                "signature": signature,
            }
        )
        return f"{self.base_url}/bridge/runs/{run_id}/artifacts?{query}"

    def verify_download(
        self, artifact_id: str, run_id: str, expires: int, signature: str, *, expand: str = ""
    ) -> None:
        if expand:
            self._verify(expires, signature, "download", artifact_id, run_id, expand, expires)
        else:
            self._verify(expires, signature, "download", artifact_id, run_id, expires)

    def verify_upload(
        self, run_id: str, kind: str, source_artifact: str, expires: int, signature: str
    ) -> None:
        self._verify(expires, signature, "upload", run_id, kind, source_artifact, expires)

    def _verify(self, expires: int, signature: str, *parts: object) -> None:
        if expires < int(time.time()):
            raise BridgeError("artifact bridge URL expired")
        expected = self._signature(*parts)
        if not hmac.compare_digest(expected, signature):
            raise BridgeError("invalid artifact bridge signature")
