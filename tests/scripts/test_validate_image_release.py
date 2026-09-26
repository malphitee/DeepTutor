"""Exercise release validation with real files, subprocesses, and workflow outputs."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_image_release.py"


def run_validator(
    tmp_path: Path,
    *,
    tag: str = "v1.2.3",
    event: str = "push",
    ref: str | None = None,
    deleted: str = "false",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    script = scripts / SCRIPT.name
    shutil.copyfile(SCRIPT, script)
    output = tmp_path / "github-output"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REF": ref if ref is not None else f"refs/tags/{tag}",
            "REF_DELETED": deleted,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output


@pytest.mark.parametrize(
    ("tag", "stable"),
    [
        ("v0.0.0", True),
        ("v1.2.3", True),
        ("v1.2.3-alpha.1", False),
        ("v1.2.3-beta.2", False),
        ("v1.2.3-rc.1", False),
        ("v1.2.3-rc.0", False),
    ],
)
def test_release_tag_is_the_authoritative_image_version(
    tmp_path: Path, tag: str, stable: bool
) -> None:
    result, output = run_validator(tmp_path, tag=tag)

    assert result.returncode == 0, result.stderr
    assert output.read_text() == (
        f"image_tag={tag[1:]}\nis_stable={str(stable).lower()}\nchannel=production\n"
    )


def test_dev_branch_push_selects_the_moving_development_image(tmp_path: Path) -> None:
    result, output = run_validator(tmp_path, ref="refs/heads/dev")

    assert result.returncode == 0, result.stderr
    assert output.read_text() == "image_tag=dev\nis_stable=false\nchannel=test\n"


@pytest.mark.parametrize(
    "tag",
    [
        "v01.2.3",
        "v1.02.3",
        "v1.2.03",
        "v1.2.3-rc.01",
        "v1.2.3-alpha.00",
        "v1.2.3-beta.02",
        "v1.2.3+build.1",
        "v1.2.3-rc.1+build.1",
        "v1.2.3rc1",
        "v1.2.3a1",
        "v1.2.3.post1",
        "v1.2.3.dev1",
        "v1.2.3-preview.1",
        "v1.2.3-rc1",
        "v1.2.3-rc.-1",
        "v1.2.3-RC.1",
        "v1.2.3-rc.1.2",
        "v1.2.3\n",
        "v1.2",
        "1.2.3",
        "v1.2.x",
        "vmain",
        "v١.2.3",
    ],
)
def test_noncanonical_release_tags_fail_without_outputs(tmp_path: Path, tag: str) -> None:
    result, output = run_validator(tmp_path, tag=tag)

    assert result.returncode != 0
    assert "Invalid release tag" in result.stderr
    assert repr(tag) in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    ("event", "ref", "deleted"),
    [
        ("release", "refs/tags/v1.2.3", "false"),
        ("workflow_dispatch", "refs/heads/dev", "false"),
        ("pull_request", "refs/heads/dev", "false"),
        ("push", "refs/tags/v1.2.3", "true"),
        ("push", "refs/heads/dev", "true"),
        ("push", "refs/heads/dev", "unexpected"),
        ("push", "refs/heads/main", "false"),
        ("push", "refs/heads/feature/user-isolation", "false"),
        ("push", "refs/heads/dev-copy", "false"),
        ("push", "refs/tags/dev", "false"),
        ("push", "", "false"),
    ],
)
def test_other_events_and_refs_cannot_publish(
    tmp_path: Path, event: str, ref: str, deleted: str
) -> None:
    result, output = run_validator(tmp_path, event=event, ref=ref, deleted=deleted)

    assert result.returncode != 0
    assert not output.exists()
