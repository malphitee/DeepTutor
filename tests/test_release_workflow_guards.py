"""Contract tests for release-tag gating in publish workflows."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOWS = {
    "docker": (
        REPOSITORY_ROOT / ".github" / "workflows" / "docker-release.yml",
        "build-and-push",
    ),
    "pypi": (
        REPOSITORY_ROOT / ".github" / "workflows" / "pypi-release.yml",
        "build-and-publish",
    ),
}


def _workflow(publication: str) -> tuple[dict, str]:
    workflow_path, publish_job_name = RELEASE_WORKFLOWS[publication]
    with workflow_path.open(encoding="utf-8") as file:
        return yaml.safe_load(file), publish_job_name


def _validator_script(publication: str) -> str:
    document, _ = _workflow(publication)
    validator = document["jobs"]["validate-release-tag"]
    return validator["steps"][0]["run"]


def _run_validator(
    publication: str, tag: str, tmp_path: Path, *, event: str = "release"
) -> subprocess.CompletedProcess[str]:
    output = tmp_path / f"{publication}-output.txt"
    return subprocess.run(
        [sys.executable, "-c", _validator_script(publication)],
        env={
            "GITHUB_EVENT_NAME": event,
            "RELEASE_TAG": tag,
            "GITHUB_OUTPUT": str(output),
            "PATH": os.environ["PATH"],
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("publication", RELEASE_WORKFLOWS)
def test_non_version_release_tags_skip_publication(publication: str) -> None:
    document, publish_job_name = _workflow(publication)
    validator = document["jobs"]["validate-release-tag"]

    if publication == "docker":
        assert validator["if"] == (
            "github.repository == 'malphitee/DeepTutor' && "
            "(github.event_name != 'release' || startsWith(github.event.release.tag_name, 'v'))"
        )
    else:
        assert validator["if"] == "startsWith(github.event.release.tag_name, 'v')"
    assert document["jobs"][publish_job_name]["needs"] == "validate-release-tag"


@pytest.mark.parametrize(
    ("publication", "tag"),
    [
        ("docker", "v1.2.3"),
        ("docker", "v1.2.3rc1"),
        ("docker", "v1.2.3+build.1"),
        ("pypi", "v1.2.3"),
        ("pypi", "v1.2.3rc1"),
        ("pypi", "v1.2.3+build.1"),
    ],
)
def test_version_release_tags_pass_the_guard(publication: str, tag: str, tmp_path: Path) -> None:
    result = _run_validator(publication, tag, tmp_path)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("publication", "tag"),
    [
        ("docker", "vmain"),
        ("docker", "v1.2"),
        ("docker", "v1.2.x"),
        ("docker", "v01.2.3"),
        ("docker", "v1.2.3+"),
        ("docker", "v1.2.3...."),
        ("pypi", "vmain"),
        ("pypi", "v1.2"),
        ("pypi", "v1.2.x"),
        ("pypi", "v01.2.3"),
        ("pypi", "v1.2.3+"),
        ("pypi", "v1.2.3...."),
    ],
)
def test_malformed_version_tags_fail_the_guard(publication: str, tag: str, tmp_path: Path) -> None:
    result = _run_validator(publication, tag, tmp_path)

    assert result.returncode != 0
    assert repr(tag) in result.stderr


def test_docker_uses_validated_tag_and_stable_latest_only(tmp_path: Path) -> None:
    result = _run_validator("docker", "v1.2.3+build.1", tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "docker-output.txt").read_text() == (
        "image_tag=1.2.3-build.1\nis_stable=false\n"
    )

    document, _ = _workflow("docker")
    metadata = next(
        step for step in document["jobs"]["build-and-push"]["steps"] if step.get("id") == "meta"
    )
    tags = metadata["with"]["tags"]
    assert "needs.validate-release-tag.outputs.image_tag" in tags
    assert "github.event.release.prerelease == false" in tags
    assert "needs.validate-release-tag.outputs.is_stable == 'true'" in tags
    assert "github.event_name == 'release'" in tags
    assert metadata["with"]["flavor"] == "latest=false"


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_branch_builds_do_not_require_a_release_or_publish_latest(event: str, tmp_path: Path):
    result = _run_validator("docker", "", tmp_path, event=event)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "docker-output.txt").read_text() == "image_tag=\nis_stable=false\n"


def test_docker_publishes_one_build_to_both_owned_registries():
    document, publish_job_name = _workflow("docker")
    # PyYAML follows YAML 1.1, where the Actions key `on` is parsed as True.
    triggers = document[True]
    assert triggers["push"]["branches"] == ["main", "feature/user-isolation"]
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers
    assert triggers["release"]["types"] == ["published"]
    assert document["permissions"]["packages"] == "write"
    assert document["concurrency"]["cancel-in-progress"] is False
    assert document["env"]["GHCR_IMAGE"] == "ghcr.io/malphitee/deeptutor"
    assert document["env"]["CNB_IMAGE"] == "docker.cnb.cool/johnnliu/deeptutor"

    steps = document["jobs"][publish_job_name]["steps"]
    logins = {
        step["with"]["registry"]: step["with"]
        for step in steps
        if step.get("uses", "").startswith("docker/login-action@")
    }
    assert logins["ghcr.io"]["password"] == "${{ secrets.GITHUB_TOKEN }}"
    assert logins["docker.cnb.cool"]["username"] == "cnb"
    assert logins["docker.cnb.cool"]["password"] == "${{ secrets.CNB_TOKEN }}"

    metadata = next(step["with"] for step in steps if step.get("id") == "meta")
    assert metadata["images"].splitlines() == ["${{ env.GHCR_IMAGE }}", "${{ env.CNB_IMAGE }}"]
    assert "type=ref,event=branch,enable=${{ github.event_name != 'release' }}" in metadata["tags"]
    assert "type=sha,format=short" in metadata["tags"]
    builders = [
        step["with"]
        for step in steps
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(builders) == 1
    assert builders[0]["push"] is True
    assert builders[0]["target"] == "production"
    assert builders[0]["platforms"] == "linux/amd64,linux/arm64"
    assert builders[0]["tags"] == "${{ steps.meta.outputs.tags }}"
