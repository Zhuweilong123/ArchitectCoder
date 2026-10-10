"""Exercise auth and workspace policy through real HTTP/WebSocket transports."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import files, testhub
from app.core import auth, security
from app.services import agent_chat_ws
from app.services.project_repository import ProjectRepository


TOKEN = "test-credential-with-at-least-32-bytes"
ORIGIN = "http://localhost:3000"


@pytest.fixture
def secured_app(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_dir = tmp_path / "projects"
    project_dir.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    settings = SimpleNamespace(internal_api_token=TOKEN, cors_origins=[ORIGIN],
                               project_dir=str(project_dir), runtime_dir=str(runtime),
                               workspace_roots=str(workspace), strict_production=True)
    for module in (auth, security, files):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(testhub, "TESTHUB_DIR", str(runtime / "testHub"))
    repository = ProjectRepository()
    monkeypatch.setattr(files, "load_project", repository.load)
    monkeypatch.setattr(files, "ensure_project_graph", lambda *args: None)
    monkeypatch.setattr(files, "save_project_with_result", repository.save)
    def save_diagram(diagram, filepath):
        Path(filepath).write_text(diagram.model_dump_json(), encoding="utf-8")
        return filepath
    monkeypatch.setattr(files, "save_diagram", save_diagram)

    class EchoSession:
        def __init__(self, websocket):
            self.websocket = websocket

        async def run(self):
            await self.websocket.send_json(await self.websocket.receive_json())

    monkeypatch.setattr(agent_chat_ws, "ChatSessionCoordinator", EchoSession)
    app = FastAPI()
    app.include_router(files.router)
    app.include_router(testhub.router)
    app.include_router(agent_chat_ws.router, prefix="/api")
    with TestClient(app) as client:
        yield client, settings, workspace, tmp_path


@pytest.mark.parametrize("endpoint", ["list", "list-projects", "browse", "open", "open-project", "validate-workspace"])
def test_file_reads_require_authentication(secured_app, endpoint):
    client, *_ = secured_app
    assert client.get(f"/api/files/{endpoint}").status_code == 401
    assert client.get(f"/api/files/{endpoint}", headers={"Authorization": "Bearer wrong"}).status_code == 403


@pytest.mark.parametrize("endpoint", ["save", "new", "save-project", "save-review", "upload/excel", "export/markdown"])
def test_file_writes_require_authentication(secured_app, endpoint):
    client, *_ = secured_app
    assert client.post(f"/api/files/{endpoint}").status_code == 401


def test_project_api_refuses_other_file_types_and_sanitizes_diagram_names(secured_app):
    client, settings, workspace, outside = secured_app
    headers = {"Authorization": f"Bearer {TOKEN}"}
    protected = workspace / ".env"
    protected.write_text("secret", encoding="utf-8")
    assert client.get("/api/files/open-project", headers=headers,
                      params={"filepath": str(protected), "safe": False}).status_code == 400
    assert client.post("/api/files/save-project", headers=headers,
                       params={"filename": str(protected), "safe": False},
                       json={"name": "overwrite", "diagrams": []}).status_code == 400
    assert protected.read_text(encoding="utf-8") == "secret"
    response = client.post("/api/files/save", headers=headers,
                           json={"name": "../../outside"})
    assert response.status_code == 200
    assert Path(response.json()["filepath"]).parent == Path(settings.project_dir)
    assert not (outside / "outside.uml").exists()


@pytest.mark.parametrize("safe", [True, False])
def test_workspace_open_save_browse_and_outside_denial(secured_app, safe):
    client, settings, workspace, outside = secured_app
    headers = {"Authorization": f"Bearer {TOKEN}"}
    project = workspace / "demo.umlproj"
    response = client.post("/api/files/save-project", headers=headers,
                           params={"filename": str(project), "safe": safe},
                           json={"name": "security-demo", "diagrams": []})
    assert response.status_code == 200, response.text
    assert client.get("/api/files/open-project", headers=headers,
                      params={"filepath": str(project), "safe": safe}).json()["project"]["name"] == "security-demo"
    listing = client.get("/api/files/browse", headers=headers,
                         params={"path": str(workspace), "safe": safe})
    assert listing.status_code == 200
    assert listing.json()["parent"] == ""
    assert listing.json()["files"][0]["name"] == "demo.umlproj"
    secret = outside / "outside.umlproj"
    secret.write_text(json.dumps({"name": "private", "diagrams": []}), encoding="utf-8")
    for endpoint, params in [("browse", {"path": str(outside)}),
                             ("open-project", {"filepath": str(secret)}),
                             ("open", {"filepath": str(secret)})]:
        assert client.get(f"/api/files/{endpoint}", headers=headers,
                          params={**params, "safe": safe}).status_code == 403
    before = secret.read_bytes()
    assert client.post("/api/files/save-project", headers=headers,
                       params={"filename": str(secret), "safe": safe},
                       json={"name": "overwrite", "diagrams": []}).status_code == 403
    assert secret.read_bytes() == before
    normalized, error = security.validate_agent_workspace_path(str(outside), kind="directory")
    assert error
    assert Path(normalized) == outside.resolve()


def test_symlink_escape_is_not_listed_or_opened(secured_app):
    client, _, workspace, outside = secured_app
    target = outside / "outside.umlproj"
    target.write_text('{"name":"private","diagrams":[]}', encoding="utf-8")
    link = workspace / "linked.umlproj"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Host does not permit symlink creation")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/files/open-project", headers=headers,
                      params={"filepath": str(link), "safe": False}).status_code == 403
    assert not client.get("/api/files/browse", headers=headers,
                          params={"path": str(workspace), "safe": False}).json()["files"]


def test_testhub_cannot_escape_through_directory_or_filename(secured_app):
    client, _, workspace, outside = secured_app
    assert client.get("/api/testhub/list").status_code == 401
    headers = {"Authorization": f"Bearer {TOKEN}"}
    denied = outside / "must-not-be-created"
    assert client.get("/api/testhub/list", headers=headers, params={"dir": str(denied)}).status_code == 403
    assert not denied.exists()
    assert client.get("/api/testhub/load", headers=headers,
                      params={"dir": str(workspace), "filename": "../private.xlsx"}).status_code == 403


def test_browser_websocket_authenticates_and_does_not_echo_credential(secured_app):
    client, *_ = secured_app
    credential = base64.urlsafe_b64encode(TOKEN.encode()).decode().rstrip("=")
    for _ in range(2):  # a fresh upgrade, including reconnects, must authenticate
        with client.websocket_connect("/api/agent/ws/chat?session_id=security-demo",
                                      subprotocols=["architectcoder", f"auth.{credential}"],
                                      headers={"origin": ORIGIN}) as ws:
            assert ws.accepted_subprotocol == "architectcoder"
            ws.send_json({"type": "chat", "message": "authenticated"})
            assert ws.receive_json()["message"] == "authenticated"


@pytest.mark.parametrize("protocols,origin,query", [
    (["architectcoder"], ORIGIN, ""),
    (["architectcoder", "auth.d3Jvbmc"], ORIGIN, ""),
    (["architectcoder", "auth.%%%"], ORIGIN, ""),
    (["architectcoder", "auth.d3Jvbmc", "auth.d3Jvbmc"], ORIGIN, ""),
    (["architectcoder"], ORIGIN, f"?token={TOKEN}"),
    (["architectcoder"], "https://untrusted.example", ""),
])
def test_websocket_rejects_missing_invalid_query_and_cross_origin_credentials(secured_app, protocols, origin, query):
    client, *_ = secured_app
    with client.websocket_connect(f"/api/agent/ws/chat{query}", subprotocols=protocols,
                                  headers={"origin": origin}) as ws:
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 1008


def test_native_websocket_bearer_compatibility_and_origin_enforcement(secured_app):
    client, *_ = secured_app
    with client.websocket_connect("/api/agent/ws/chat", headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
        ws.send_json({"message": "native"})
        assert ws.receive_json()["message"] == "native"
    with client.websocket_connect("/api/agent/ws/chat", headers={
            "Authorization": f"Bearer {TOKEN}", "origin": "https://untrusted.example"}) as ws:
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 1008


def test_local_no_token_mode_still_works_and_checks_browser_origin(secured_app):
    client, settings, workspace, _ = secured_app
    settings.internal_api_token = ""
    settings.strict_production = False
    assert client.get("/api/files/browse", params={"path": str(workspace)}).status_code == 200
    with client.websocket_connect("/api/agent/ws/chat", subprotocols=["architectcoder"],
                                  headers={"origin": ORIGIN}) as ws:
        ws.send_json({"message": "local development"})
        assert ws.receive_json()["message"] == "local development"
    with client.websocket_connect("/api/agent/ws/chat",
                                  headers={"origin": "https://untrusted.example"}) as ws:
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 1008
