"""Minimal Orkes Conductor REST client (stdlib only) for registration, execution and trace reads.

Uses documented endpoints: POST /api/token, PUT /api/metadata/workflow, POST|PUT /api/metadata/taskdefs,
POST /api/workflow/execute/{name}/{version}, POST /api/workflow/{name}, GET /api/workflow/{id}.
Workers use the official conductor-python SDK (see workers.py).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .config import ConductorSettings


class ConductorError(RuntimeError):
    pass


class ConductorREST:
    def __init__(self, settings: ConductorSettings | None = None):
        self.s = settings or ConductorSettings.from_env()
        self._token: str | None = None
        self._token_at = 0.0

    def _auth(self) -> str:
        if not self._token or time.time() - self._token_at > 1800:
            body = {"keyId": self.s.key_id, "keySecret": self.s.key_secret}
            self._token = self._raw("POST", "/token", body, auth=False)["token"]
            self._token_at = time.time()
        return self._token

    def _raw(self, method: str, path: str, body: Any = None, auth: bool = True, timeout: float = 60) -> Any:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            headers["X-Authorization"] = self._auth()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.s.server_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:2000]
            raise ConductorError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    # metadata
    def register_task_defs(self, defs: list[dict]) -> None:
        existing = {d["name"] for d in (self._raw("GET", "/metadata/taskdefs") or [])}
        new = [d for d in defs if d["name"] not in existing]
        if new:
            self._raw("POST", "/metadata/taskdefs", new)
        for d in defs:
            if d["name"] in existing:
                self._raw("PUT", "/metadata/taskdefs", d)

    def register_workflows(self, defs: list[dict]) -> None:
        self._raw("PUT", "/metadata/workflow", defs)

    def get_workflow_def(self, name: str, version: int | None = None) -> dict:
        q = f"?version={version}" if version else ""
        return self._raw("GET", f"/metadata/workflow/{name}{q}")

    # execution
    def execute(self, name: str, version: int, inp: dict, wait_seconds: int = 30, correlation_id: str | None = None) -> dict:
        """Synchronous execution; returns the run snapshot (may still be RUNNING if wait elapses)."""
        body = {"name": name, "version": version, "input": inp}
        if correlation_id:
            body["correlationId"] = correlation_id
        q = f"?waitForSeconds={wait_seconds}"
        return self._raw("POST", f"/workflow/execute/{name}/{version}{q}", body, timeout=wait_seconds + 30)

    def start(self, name: str, version: int, inp: dict, correlation_id: str | None = None) -> str:
        body = {"name": name, "version": version, "input": inp, "correlationId": correlation_id}
        return self._raw("POST", "/workflow", body)

    def get_execution(self, workflow_id: str, include_tasks: bool = True) -> dict:
        return self._raw("GET", f"/workflow/{workflow_id}?includeTasks={'true' if include_tasks else 'false'}")

    def wait_for(self, workflow_id: str, timeout_s: float = 120, poll_s: float = 0.5) -> dict:
        deadline = time.time() + timeout_s
        while True:
            wf = self.get_execution(workflow_id)
            if wf.get("status") not in ("RUNNING", "PAUSED"):
                return wf
            if time.time() > deadline:
                return wf
            time.sleep(poll_s)
