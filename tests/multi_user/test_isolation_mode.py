"""Startup must announce which isolation posture actually booted."""

from __future__ import annotations

import logging

from deeptutor.services import auth as auth_service


def test_auth_disabled_announces_single_user_compatibility(caplog, monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", False)
    with caplog.at_level(logging.INFO, logger="deeptutor.services.auth"):
        auth_service.log_isolation_mode()

    message = next(
        (record.message for record in caplog.records if "Isolation mode" in record.message),
        None,
    )
    assert message is not None
    assert "single-user compatibility" in message
    assert "multi-user" not in message


def test_auth_enabled_announces_multi_user_isolation(caplog, monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.delenv("DEEPTUTOR_WORKSPACE_ROOT", raising=False)
    with caplog.at_level(logging.INFO, logger="deeptutor.services.auth"):
        auth_service.log_isolation_mode()

    message = next(
        (record.message for record in caplog.records if "Isolation mode" in record.message),
        None,
    )
    assert message is not None
    assert "multi-user isolated" in message


def test_shared_workspace_root_warns_under_multi_user(caplog, monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setenv("DEEPTUTOR_WORKSPACE_ROOT", "/srv/shared")
    with caplog.at_level(logging.WARNING, logger="deeptutor.services.auth"):
        auth_service.log_isolation_mode()

    warnings = [
        record.message
        for record in caplog.records
        if record.levelno == logging.WARNING and "DEEPTUTOR_WORKSPACE_ROOT" in record.message
    ]
    assert warnings, "a deployment-wide shared root must warn when auth is enabled"
