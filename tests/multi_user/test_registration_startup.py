"""Registration safety checks run before the API starts its other services."""

from __future__ import annotations

from fastapi import FastAPI
import pytest


class _RegistrationPreflightComplete(Exception):
    """Stop before unrelated service startup after admission checks have passed."""


@pytest.fixture
def registration_startup(mu_isolated_root, monkeypatch):
    # Import only after the isolated data tree is installed: main initializes
    # runtime directories at import time, before the lifespan is entered.
    from deeptutor.api import main as api_main
    from deeptutor.services import auth as auth_service
    from deeptutor.services import config as config_service

    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_service, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(config_service, "load_system_settings", lambda: {"backend_workers": 1})

    def preflight_complete():
        raise _RegistrationPreflightComplete

    monkeypatch.setattr(api_main, "validate_tool_consistency", preflight_complete)
    return api_main.lifespan


@pytest.mark.asyncio
@pytest.mark.parametrize("workers", [2, 4])
async def test_multi_user_startup_rejects_multiple_workers_before_creating_key(
    registration_startup, monkeypatch, workers
):
    from deeptutor.multi_user import identity
    from deeptutor.multi_user.registration_limits import PROXY_SECRET_FILENAME
    from deeptutor.services import config as config_service

    monkeypatch.setattr(
        config_service, "load_system_settings", lambda: {"backend_workers": workers}
    )
    app = FastAPI()

    with pytest.raises(RuntimeError, match="backend_workers=1"):
        async with registration_startup(app):
            pytest.fail("An unsafe worker configuration reached API readiness")

    assert app.state.ready is False
    assert not (identity.AUTH_DIR / PROXY_SECRET_FILENAME).exists()


@pytest.mark.asyncio
async def test_single_worker_startup_creates_and_reuses_bridge_key_before_services(
    registration_startup,
):
    from deeptutor.multi_user import identity
    from deeptutor.multi_user.registration_limits import PROXY_SECRET_FILENAME, proxy_secret

    path = identity.AUTH_DIR / PROXY_SECRET_FILENAME
    assert not path.exists()
    app = FastAPI()

    with pytest.raises(_RegistrationPreflightComplete):
        async with registration_startup(app):
            pytest.fail("The test must stop before unrelated services start")

    secret = proxy_secret()
    assert path.exists()
    assert app.state.ready is False

    with pytest.raises(_RegistrationPreflightComplete):
        async with registration_startup(FastAPI()):
            pytest.fail("The test must stop before unrelated services start")

    assert proxy_secret() == secret


@pytest.mark.asyncio
async def test_multi_user_startup_rejects_corrupt_bridge_key(registration_startup):
    from deeptutor.multi_user import identity
    from deeptutor.multi_user.registration_limits import PROXY_SECRET_FILENAME

    path = identity.AUTH_DIR / PROXY_SECRET_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken", encoding="ascii")
    app = FastAPI()

    with pytest.raises(identity.IdentityStoreError):
        async with registration_startup(app):
            pytest.fail("A corrupt bridge key reached API readiness")

    assert app.state.ready is False
    assert path.read_text(encoding="ascii") == "broken"


@pytest.mark.asyncio
async def test_single_user_startup_does_not_require_bridge_key_or_single_worker(
    registration_startup, monkeypatch
):
    from deeptutor.multi_user import identity
    from deeptutor.multi_user.registration_limits import PROXY_SECRET_FILENAME
    from deeptutor.services import auth as auth_service
    from deeptutor.services import config as config_service

    monkeypatch.setattr(auth_service, "AUTH_ENABLED", False)
    monkeypatch.setattr(config_service, "load_system_settings", lambda: {"backend_workers": 4})

    with pytest.raises(_RegistrationPreflightComplete):
        async with registration_startup(FastAPI()):
            pytest.fail("The test must stop before unrelated services start")

    assert not (identity.AUTH_DIR / PROXY_SECRET_FILENAME).exists()
