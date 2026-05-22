import hashlib

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from spillover.proxy.middleware import ProjectIdMiddleware


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(ProjectIdMiddleware)

    @app.get("/echo")
    async def echo(request: Request):
        return JSONResponse({"project_id": request.state.project_id})

    return TestClient(app)


def test_middleware_passes_x_project(client):
    r = client.get("/echo", headers={"X-Project": "deadbeef"})
    assert r.status_code == 200
    assert r.json()["project_id"] == "deadbeef"


def test_middleware_hashes_arbitrary_path_when_unhashed(client):
    raw = "/Users/luiz/Documents/Projects/agente-imobiliaria"
    expected = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    r = client.get("/echo", headers={"X-Project": raw})
    assert r.json()["project_id"] == expected


def test_middleware_falls_back_to_default_project(client, monkeypatch):
    """When no /p/, header, or env is given, middleware uses 'default' project."""
    monkeypatch.delenv("SPILLOVER_PROJECT_ID", raising=False)
    monkeypatch.delenv("SPILLOVER_DEFAULT_PROJECT_ID", raising=False)
    r = client.get("/echo")
    assert r.status_code == 200
    assert r.json()["project_id"] == hashlib.sha1(b"default").hexdigest()


def test_middleware_default_overridable(client, monkeypatch):
    monkeypatch.delenv("SPILLOVER_PROJECT_ID", raising=False)
    monkeypatch.setenv("SPILLOVER_DEFAULT_PROJECT_ID", "myhouse")
    r = client.get("/echo")
    assert r.status_code == 200
    assert r.json()["project_id"] == hashlib.sha1(b"myhouse").hexdigest()


def test_middleware_falls_back_to_env(client, monkeypatch):
    monkeypatch.setenv("SPILLOVER_PROJECT_ID", "deadbeefcafe")
    r = client.get("/echo")
    assert r.status_code == 200
    assert r.json()["project_id"] == "deadbeefcafe"
