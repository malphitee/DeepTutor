"""Regression coverage for tag-derived Docker application versions."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_release_validator_uses_the_tag_without_reading_source_version(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(
        REPOSITORY_ROOT / "scripts" / "validate_image_release.py",
        scripts / "validate_image_release.py",
    )
    output = tmp_path / "github-output"

    result = subprocess.run(
        [sys.executable, str(scripts / "validate_image_release.py")],
        cwd=repository,
        env={
            "PATH": os.environ["PATH"],
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/tags/v9.8.7",
            "REF_DELETED": "false",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text() == (
        "image_tag=9.8.7\nis_stable=true\nchannel=production\n"
    )


def test_release_workflow_injects_validated_tag_into_the_docker_build() -> None:
    workflow = yaml.safe_load(
        (REPOSITORY_ROOT / ".github/workflows/docker-release.yml").read_text()
    )
    builder = next(
        step["with"]
        for step in workflow["jobs"]["build-and-push"]["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
    )

    assert builder["build-args"].splitlines() == [
        "APP_VERSION=${{ needs.validate-release-tag.outputs.image_tag }}"
    ]


def test_frontend_build_prefers_the_injected_version() -> None:
    result = subprocess.run(
        [
            "node",
            "-e",
            "const config = require('./next.config.js'); "
            "process.stdout.write(config.env.NEXT_PUBLIC_APP_VERSION);",
        ],
        cwd=REPOSITORY_ROOT / "web",
        env={**os.environ, "NEXT_PUBLIC_APP_VERSION": "9.8.7"},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == "9.8.7"


def test_backend_runtime_prefers_the_injected_version(monkeypatch) -> None:
    version_module = importlib.import_module("deeptutor.__version__")
    resolver = getattr(version_module, "get_runtime_version", None)
    assert callable(resolver)

    monkeypatch.setenv("DEEPTUTOR_APP_VERSION", "9.8.7")
    assert resolver() == "9.8.7"

    from deeptutor.services import app_update

    monkeypatch.setattr(app_update, "_running_in_container", lambda: True)
    installation = app_update.detect_installation()
    assert installation.mode == "docker"
    assert installation.current_version == "9.8.7"

    monkeypatch.setenv("DEEPTUTOR_APP_VERSION", "  ")
    assert resolver() == version_module.__version__


def test_dockerfile_carries_the_build_version_into_both_runtimes() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text()

    assert dockerfile.count("ARG APP_VERSION") >= 3
    assert "NEXT_PUBLIC_APP_VERSION=${APP_VERSION}" in dockerfile
    assert "DEEPTUTOR_APP_VERSION=${APP_VERSION}" in dockerfile
