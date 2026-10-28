"""Opt-in single-user authentication for the engine API.

Covers the spec's session lifecycle: credentials come from env vars, login
exchanges them for a token, logout invalidates it, the auth middleware guards
every HTTP route and the WebSocket, and everything stays open when auth is off.
"""

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

USER = "admin"
PASSWORD = "hunter2"


def make_app(tmp_path: Path):
    pytest.importorskip("fastapi")
    pytest.importorskip("yaml")
    pytest.importorskip("numpy")
    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    return create_app(config, settings)


def _client(tmp_path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    return TestClient(make_app(tmp_path), base_url="http://127.0.0.1")


@pytest.fixture
def auth_client(monkeypatch, tmp_path):
    """An app with auth enabled (both env vars set)."""
    monkeypatch.setenv("CLIPS_STUDIO_AUTH_USER", USER)
    monkeypatch.setenv("CLIPS_STUDIO_AUTH_PASSWORD", PASSWORD)
    return _client(tmp_path)


@pytest.fixture
def open_client(monkeypatch, tmp_path):
    """An app with auth disabled (no env vars) — today's default behaviour."""
    monkeypatch.delenv("CLIPS_STUDIO_AUTH_USER", raising=False)
    monkeypatch.delenv("CLIPS_STUDIO_AUTH_PASSWORD", raising=False)
    return _client(tmp_path)


def _login(client, user=USER, password=PASSWORD):
    return client.post("/login", json={"username": user, "password": password})


# ---- auth is opt-in via env vars -------------------------------------------


@pytest.mark.parametrize(
    ("username", "password", "enabled"),
    [
        (None, None, False),
        ("admin", None, False),
        (None, "secret", False),
        ("", "secret", False),
        ("admin", "", False),
        ("admin", "secret", True),
    ],
)
def test_auth_requires_both_non_empty_credentials(monkeypatch, tmp_path, username, password, enabled):
    for name, value in (
        ("CLIPS_STUDIO_AUTH_USER", username),
        ("CLIPS_STUDIO_AUTH_PASSWORD", password),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    app = make_app(tmp_path)

    assert app.state.auth_enabled is enabled


# ---- login / logout / session lifecycle ------------------------------------


def test_login_success_returns_a_token(auth_client):
    body = _login(auth_client).json()
    assert body["token"]
    assert isinstance(body["token"], str)


def test_login_failure_returns_401_without_a_token(auth_client):
    res = _login(auth_client, password="wrong")
    assert res.status_code == 401
    assert "token" not in res.json()


def test_logout_invalidates_the_token(auth_client):
    token = _login(auth_client).json()["token"]
    assert auth_client.get("/creators", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    auth_client.post("/logout", headers={"Authorization": f"Bearer {token}"})

    assert auth_client.get("/creators", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_login_while_auth_disabled_creates_no_session(open_client):
    body = _login(open_client).json()
    assert body == {"token": None}


# ---- health reports auth state, and stays open -----------------------------


def test_health_reports_auth_enabled(auth_client):
    body = auth_client.get("/health").json()
    assert body["auth_enabled"] is True


def test_health_reports_auth_disabled(open_client):
    body = open_client.get("/health").json()
    assert body["auth_enabled"] is False


# ---- protected routes ------------------------------------------------------


def test_protected_route_401_without_a_token(auth_client):
    assert auth_client.get("/creators").status_code == 401


def test_protected_route_served_with_a_token(auth_client):
    token = _login(auth_client).json()["token"]
    assert auth_client.get("/creators", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_installed_route_is_equally_protected(auth_client):
    # youtube_api.install() adds /youtube/status; the middleware must cover it
    # too, not just the inline routes.
    assert auth_client.get("/youtube/status").status_code == 401
    token = _login(auth_client).json()["token"]
    assert auth_client.get("/youtube/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_auth_disabled_passthrough(open_client):
    # No token, no credentials: every route answers exactly as before.
    assert open_client.get("/creators").status_code == 200
    assert open_client.get("/youtube/status").status_code == 200


def test_trustedhost_still_refuses_a_bad_host_under_auth(auth_client):
    # The auth middleware runs innermost, so a request addressed to a foreign
    # Host is refused by TrustedHost BEFORE a token is even considered — even a
    # valid token cannot reach a route through a rebinding-style Host header.
    token = _login(auth_client).json()["token"]
    res = auth_client.get(
        "/creators", headers={"Host": "evil.example", "Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 400


# ---- WebSocket -------------------------------------------------------------


def test_ws_rejected_without_a_token(auth_client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with auth_client.websocket_connect("ws://127.0.0.1/ws"):
            pass


def test_ws_rejected_with_an_invalid_token(auth_client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with auth_client.websocket_connect("ws://127.0.0.1/ws?token=not-a-real-token"):
            pass


def test_ws_accepted_with_a_valid_token(auth_client):
    token = _login(auth_client).json()["token"]
    with auth_client.websocket_connect(f"ws://127.0.0.1/ws?token={token}"):
        pass  # reaching here means the handshake was accepted


def test_ws_accepted_when_auth_is_disabled(open_client):
    with open_client.websocket_connect("ws://127.0.0.1/ws"):
        pass
