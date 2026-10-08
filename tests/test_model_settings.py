"""Settings protocol tests use fixture credentials only; no provider is called."""

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from research_workspace.accounts import Accounts
from research_workspace.model_settings import ALLOWED_MODELS, DEEPSEEK_BASE_URL, DEFAULT_MODEL, router


FIXTURE_KEY = "fixture-key-never-a-real-credential"


@pytest.fixture
def settings_app(tmp_path, monkeypatch):
    for name in ("RESEARCH_MODEL_BASE_URL", "RESEARCH_MODEL", "RESEARCH_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    app = FastAPI()
    app.state.accounts = Accounts(tmp_path)
    app.state.calls = []

    def configure(api_key, model):
        app.state.calls.append((api_key, model))
        monkeypatch.setenv("RESEARCH_MODEL_BASE_URL", DEEPSEEK_BASE_URL)
        monkeypatch.setenv("RESEARCH_MODEL", model)
        monkeypatch.setenv("RESEARCH_MODEL_API_KEY", api_key)
        return {"unsafe_callback_return": api_key}

    app.state.configure_model = configure
    app.include_router(router)
    return app


def client(app):
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_safe_get_does_not_expose_credentials(settings_app):
    with client(settings_app) as browser:
        initial = browser.get("/api/model")
        assert initial.json() == {"configured": False, "provider": "DeepSeek", "defaultModel": DEFAULT_MODEL}
        saved = browser.post("/api/model", json={"api_key": FIXTURE_KEY, "model": DEFAULT_MODEL})
        assert saved.status_code == 200 and saved.json()["configured"] is True
        assert FIXTURE_KEY not in saved.text and "unsafe_callback_return" not in saved.text
        current = browser.get("/api/model")
        assert current.json() == {"configured": True, "provider": "DeepSeek", "defaultModel": DEFAULT_MODEL}
        assert current.headers["cache-control"] == "no-store"
        assert FIXTURE_KEY not in current.text
    assert settings_app.state.calls == [(FIXTURE_KEY, DEFAULT_MODEL)]


@pytest.mark.parametrize("model", ALLOWED_MODELS)
def test_explicit_supported_models_pass_to_application(settings_app, model):
    with client(settings_app) as browser:
        response = browser.post("/api/model", json={"api_key": FIXTURE_KEY, "model": model})
    assert response.status_code == 200
    assert settings_app.state.calls == [(FIXTURE_KEY, model)]


@pytest.mark.parametrize("bad_key", ["short", "x" * 301, "fixture key", "fixture\nkey", "fixture\x00key", "fixture\u200bkey", " " * 10])
def test_invalid_key_never_appears_in_error_or_callback(settings_app, bad_key):
    with client(settings_app) as browser:
        response = browser.post("/api/model", json={"api_key": bad_key, "model": DEFAULT_MODEL})
    assert response.status_code == 400
    assert "input" not in response.json() and "api_key" not in response.text
    assert settings_app.state.calls == []


@pytest.mark.parametrize("payload", [
    {"api_key": FIXTURE_KEY, "model": "unsupported-model"},
    {"api_key": FIXTURE_KEY, "base_url": "https://elsewhere.invalid"},
    {"api_key": 12345678, "model": DEFAULT_MODEL},
    [FIXTURE_KEY],
])
def test_invalid_schema_has_safe_error_without_raw_input(settings_app, payload):
    with client(settings_app) as browser:
        response = browser.post("/api/model", json=payload)
    assert response.status_code == 400 and FIXTURE_KEY not in response.text
    assert settings_app.state.calls == []


def test_nondefault_account_cannot_read_or_change_global_provider(settings_app):
    token, _ = settings_app.state.accounts.register("settings-test", "fixture-password-123")
    with client(settings_app) as browser:
        browser.cookies.set(Accounts.COOKIE, token)
        assert browser.get("/api/model").status_code == 403
        assert browser.post("/api/model", json={"api_key": FIXTURE_KEY}).status_code == 403
    assert settings_app.state.calls == []


def test_expired_session_is_not_treated_as_default_space(settings_app):
    with client(settings_app) as browser:
        browser.cookies.set(Accounts.COOKIE, "invalid-fixture-session")
        assert browser.get("/api/model").status_code == 401
        assert browser.post("/api/model", json={"api_key": FIXTURE_KEY}).status_code == 401
    assert settings_app.state.calls == []


def test_callback_exception_is_sanitized(settings_app):
    def failing(api_key, model):
        raise RuntimeError("private failure " + api_key)
    settings_app.state.configure_model = failing
    with client(settings_app) as browser:
        response = browser.post("/api/model", json={"api_key": FIXTURE_KEY})
    assert response.status_code == 503
    assert FIXTURE_KEY not in response.text and "private failure" not in response.text


def test_missing_callback_is_reported_safely(settings_app):
    del settings_app.state.configure_model
    with client(settings_app) as browser:
        response = browser.post("/api/model", json={"api_key": FIXTURE_KEY})
    assert response.status_code == 503 and FIXTURE_KEY not in response.text


def test_async_callback_supported(settings_app):
    original = settings_app.state.configure_model
    async def configure(api_key, model):
        original(api_key, model)
    settings_app.state.configure_model = configure
    with client(settings_app) as browser:
        assert browser.post("/api/model", json={"api_key": FIXTURE_KEY}).status_code == 200
    assert settings_app.state.calls == [(FIXTURE_KEY, DEFAULT_MODEL)]


def test_setting_body_is_bounded_and_never_logged(settings_app, caplog):
    with client(settings_app) as browser:
        too_large = browser.post("/api/model", content=b"x" * 4097, headers={"Content-Type": "application/json"})
        malformed = browser.post("/api/model", content=FIXTURE_KEY, headers={"Content-Type": "application/json"})
        wrong_type = browser.post("/api/model", content=FIXTURE_KEY)
        assert too_large.status_code == 413 and malformed.status_code == 400 and wrong_type.status_code == 415
    assert FIXTURE_KEY not in caplog.text
    assert settings_app.state.calls == []


def test_router_does_not_write_key_to_disk(settings_app):
    with client(settings_app) as browser:
        assert browser.post("/api/model", json={"api_key": FIXTURE_KEY}).status_code == 200
    assert os.environ["RESEARCH_MODEL_API_KEY"] == FIXTURE_KEY
    for file in settings_app.state.accounts.root.rglob("*"):
        if file.is_file():
            assert FIXTURE_KEY.encode() not in file.read_bytes()
