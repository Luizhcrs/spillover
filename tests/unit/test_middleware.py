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


def test_middleware_400_when_missing(client, monkeypatch):
    monkeypatch.delenv("SPILLOVER_PROJECT_ID", raising=False)
    r = client.get("/echo")
    assert r.status_code == 400
    assert "X-Project" in r.text


def test_middleware_falls_back_to_env(client, monkeypatch):
    monkeypatch.setenv("SPILLOVER_PROJECT_ID", "deadbeefcafe")
    r = client.get("/echo")
    assert r.status_code == 200
    assert r.json()["project_id"] == "deadbeefcafe"
