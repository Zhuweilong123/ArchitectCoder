"""Exercise auth and workspace policy through real HTTP/WebSocket transports."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import files
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
    app.include_router(agent_chat_ws.router, prefix="/api")
    with TestClient(app) as client:
        yield client, settings, workspace, tmp_path


@pytest.mark.parametrize("endpoint", ["list", "list-projects", "browse", "browse-roots", "open", "open-project", "validate-workspace"])
def test_file_reads_require_authentication(secured_app, endpoint):
    client, *_ = secured_app
    assert client.get(f"/api/files/{endpoint}").status_code == 401
    assert client.get(f"/api/files/{endpoint}", headers={"Authorization": "Bearer wrong"}).status_code == 403


@pytest.mark.parametrize("endpoint", ["save", "new", "save-project", "save-review", "export/markdown"])
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


def test_directory_picker_roots_are_navigable_without_exposing_parent_drives(secured_app):
    client, settings, workspace, outside = secured_app
    headers = {"Authorization": f"Bearer {TOKEN}"}
    # Include a repeated root and a stale configuration entry.
    settings.workspace_roots = f"{workspace},{workspace},{outside / 'missing'}"
    response = client.get("/api/files/browse-roots", headers=headers)
    assert response.status_code == 200
    roots = response.json()["roots"]
    assert {root["path"] for root in roots} == {
        Path(settings.project_dir).as_posix(), Path(settings.runtime_dir).as_posix(), workspace.as_posix(),
    }
    assert len(roots) == 3
    for root in roots:
        listing = client.get("/api/files/browse", headers=headers, params={"path": root["path"], "safe": False})
        assert listing.status_code == 200
        assert listing.json()["current"] == root["path"]
    assert client.get("/api/files/browse", headers=headers,
                      params={"path": str(outside), "safe": False}).status_code == 403


@pytest.mark.parametrize("safe", [True, False])
@pytest.mark.parametrize("authenticated", [True, False])
def test_local_project_selection_browses_disks_and_opens_saves_unconfigured_projects(
    secured_app, safe, authenticated,
):
    client, settings, _, outside = secured_app
    settings.strict_production = False
    settings.internal_api_token = TOKEN if authenticated else ""
    headers = {"Authorization": f"Bearer {TOKEN}"} if authenticated else {}
    # A different project, outside every configured root, including spaces/Unicode.
    selected = outside / "另一个 project"
    selected.mkdir()
    design = selected / "design"
    design.mkdir()
    project = design / "demo.umlproj"
    roots = client.get("/api/files/browse-roots", headers=headers).json()["roots"]
    disk = Path(selected.anchor)
    assert disk.as_posix() in {root["path"] for root in roots}
    listing = client.get("/api/files/browse", headers=headers, params={"path": str(disk), "safe": safe})
    assert listing.status_code == 200
    assert listing.json()["current"] == disk.as_posix()
    listing = client.get("/api/files/browse", headers=headers, params={"path": str(selected), "safe": safe})
    assert listing.status_code == 200
    assert listing.json()["parent"] == outside.as_posix()
    assert listing.json()["dirs"] == [{"name": "design", "path": design.as_posix()}]
    saved = client.post("/api/files/save-project", headers=headers,
                        params={"filename": str(project), "safe": safe},
                        json={"name": "external-project", "diagrams": []})
    assert saved.status_code == 200, saved.text
    opened = client.get("/api/files/open-project", headers=headers,
                        params={"filepath": str(project), "safe": safe})
    assert opened.status_code == 200, opened.text
    assert opened.json()["project"]["name"] == "external-project"
    for path, kind in [(selected, "directory"), (project, "file")]:
        validation = client.get("/api/files/validate-workspace", headers=headers,
                                params={"path": str(path), "kind": kind})
        assert validation.json()["valid"] is True
        assert Path(validation.json()["path"]) == path.resolve()
    missing = client.get("/api/files/validate-workspace", headers=headers,
                         params={"path": str(selected / "missing"), "kind": "directory"})
    assert missing.json()["valid"] is False

    # Selecting an external project does not grant Agent tools the whole disk.
    from app.core.capabilities import CapabilityPolicy
    from app.runtime.workspace import WorkspaceManifest
    manifest = WorkspaceManifest.from_paths(workspace_root=str(selected))
    policy = CapabilityPolicy(workspace_roots=list(manifest.workspace_roots))
    assert policy.check("read_file", {"path": str(project)}) is None
    assert policy.check("read_file", {"path": str(outside / "other-project.txt")}) is not None


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
