"""No-login mode (AUTH_REQUIRED=false): one local user, this computer only."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.deps import LOCAL_USER_EMAIL, CurrentUser
from app.core.errors import register_exception_handlers
from app.models import User

CSRF = {"X-Requested-With": "ClipFusion"}


@pytest.fixture()
def no_login(monkeypatch) -> TestClient:
    monkeypatch.setattr(get_settings(), "auth_required", False)
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/whoami")
    @app.post("/whoami")
    def whoami(user: CurrentUser) -> dict:
        return {"id": str(user.id), "email": user.email}

    return TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))


def test_requests_work_without_credentials_as_one_user(no_login, db):
    first = no_login.get("/whoami")
    second = no_login.post("/whoami", headers=CSRF)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["email"] == LOCAL_USER_EMAIL
    assert db.scalar(select(func.count()).select_from(User).where(User.email == LOCAL_USER_EMAIL)) == 1


def test_other_sites_cannot_send_commands(no_login):
    assert no_login.post("/whoami").json()["error"]["code"] == "csrf_failed"


@pytest.mark.parametrize("host", ["evil.example", "192.168.1.20:8000", "localhost.evil.example"])
def test_only_this_computer_is_served(no_login, host):
    resp = no_login.get("/whoami", headers={"Host": host})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "local_only"


@pytest.mark.parametrize("peer", ["192.168.1.50", "10.0.0.7", "203.0.113.9", "fe80::1", "testclient"])
def test_another_machine_cannot_get_in_by_faking_the_host_header(no_login, peer):
    remote = TestClient(no_login.app, base_url="http://localhost:8000", client=(peer, 50000))
    assert remote.get("/whoami", headers={"Host": "localhost"}).json()["error"]["code"] == "local_only"
    assert remote.post("/whoami", headers={"Host": "localhost", **CSRF}).status_code == 403
    assert remote.get("/whoami", headers={"Host": "localhost", "X-Forwarded-For": "127.0.0.1"}).status_code == 403


@pytest.mark.parametrize("peer", ["127.0.0.1", "127.0.0.5", "::1", "::ffff:127.0.0.1"])
def test_loopback_peers_are_served(no_login, peer):
    assert TestClient(no_login.app, base_url="http://localhost:8000", client=(peer, 1)).get("/whoami").status_code == 200


def test_docker_gateway_must_be_listed_explicitly(no_login, monkeypatch):
    container = TestClient(no_login.app, base_url="http://localhost:8000", client=("172.18.0.1", 1))
    assert container.get("/whoami").status_code == 403
    monkeypatch.setattr(get_settings(), "no_login_trusted_networks", ["172.16.0.0/12"])
    assert container.get("/whoami").status_code == 200


def test_login_is_still_enforced_when_required(no_login, monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_required", True)
    assert no_login.get("/whoami").status_code == 401


def test_production_refuses_to_run_without_login():
    with pytest.raises(ValidationError, match="AUTH_REQUIRED"):
        Settings(environment="production", auth_required=False)
