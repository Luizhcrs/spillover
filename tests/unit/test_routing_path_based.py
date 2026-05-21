import hashlib

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from spillover.proxy.middleware import ProjectIdMiddleware


@pytest.fixture
def app_client():
    app = FastAPI()
    app.add_middleware(ProjectIdMiddleware)

    @app.post("/v1/messages")
    async def messages(request: Request):
        return JSONResponse({"project_id": request.state.project_id, "path": request.url.path})

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return TestClient(app)


def test_path_based_routing_hex_id(app_client):
    pid = "abcdef1234"
    r = app_client.post(f"/p/{pid}/v1/messages", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == pid


def test_path_based_routing_hashes_non_hex(app_client):
    raw = "my-project"
    r = app_client.post(f"/p/{raw}/v1/messages", json={})
    assert r.status_code == 200
    expected = hashlib.sha1(raw.encode()).hexdigest()
    assert r.json()["project_id"] == expected


def test_header_still_works(app_client):
    r = app_client.post("/v1/messages", json={}, headers={"X-Project": "abcdef12"})
    assert r.status_code == 200
    assert r.json()["project_id"] == "abcdef12"


def test_no_project_anywhere_returns_400(app_client, monkeypatch):
    monkeypatch.delenv("SPILLOVER_PROJECT_ID", raising=False)
    r = app_client.post("/v1/messages", json={})
    assert r.status_code == 400


def test_health_exempt_from_path_check(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
