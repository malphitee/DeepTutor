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
    version_source: str | None = '__version__ = "1.2.3"\n',
    event: str = "push",
    ref: str | None = None,
    deleted: str = "false",
    without_site_packages: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    script = scripts / SCRIPT.name
    shutil.copyfile(SCRIPT, script)
    if version_source is not None:
        version_directory = repository / "deeptutor"
        version_directory.mkdir(exist_ok=True)
        (version_directory / "__version__.py").write_text(version_source, encoding="utf-8")
    output = tmp_path / "github-output"
    command = [sys.executable]
    if without_site_packages:
        command.append("-S")
    result = subprocess.run(
        [*command, str(script)],
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
    ("tag", "application_version", "stable"),
    [
        ("v0.0.0", "0.0.0", True),
        ("v1.2.3", "1.2.3", True),
        ("v1.2.3-alpha.1", "1.2.3a1", False),
        ("v1.2.3-beta.2", "1.2.3b2", False),
        ("v1.2.3-rc.1", "1.2.3rc1", False),
        ("v1.2.3-rc.0", "1.2.3rc0", False),
    ],
)
def test_release_tag_matches_normalized_application_version(
    tmp_path: Path, tag: str, application_version: str, stable: bool
) -> None:
    result, output = run_validator(
        tmp_path, tag=tag, version_source=f"__version__ = {application_version!r}\n"
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text() == (
        f"image_tag={tag[1:]}\nis_stable={str(stable).lower()}\nchannel=production\n"
    )


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


@pytest.mark.parametrize("version", ["1.2.4", "1.2.3rc1", "1.2.3.post1", "1.2.3+build.1"])
def test_application_version_mismatch_preserves_existing_outputs(
    tmp_path: Path, version: str
) -> None:
    output = tmp_path / "github-output"
    output.write_text("previous_step=value\n", encoding="utf-8")
    result, _ = run_validator(tmp_path, version_source=f"__version__ = {version!r}\n")

    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert repr(version) in result.stderr
    assert output.read_text() == "previous_step=value\n"


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (None, "Cannot read application version"),
        ("OTHER = '1.2.3'\n", "found 0"),
        ('__version__ = "1.2.3"\n__version__ = "1.2.3"\n', "found 2"),
        ('__version__ = "1.2.3"\n__version__ += "rc1"\n', "found 2"),
        ('__version__ = "1.2.3"\nif True:\n    __version__ = "1.2.4"\n', "found 2"),
        ("__version__ = 123\n", "literal string assignment"),
        ('__version__ = "1.2." + "3"\n', "literal string assignment"),
        ('if True:\n    __version__ = "1.2.3"\n', "top-level"),
        ("__version__ = [\n", "Cannot read application version"),
        ('__version__ = "not-a-version"\n', "Invalid application __version__"),
        ('__version__ = ""\n', "Invalid application __version__"),
    ],
)
def test_missing_ambiguous_or_invalid_application_version_fails_without_outputs(
    tmp_path: Path, source: str | None, error: str
) -> None:
    result, output = run_validator(tmp_path, version_source=source)

    assert result.returncode != 0
    assert error in result.stderr
    assert not output.exists()


def test_version_file_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "application-code-was-executed"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
        "raise RuntimeError('application import is forbidden')\n"
        '__version__: str = "1.2.3"\n'
    )
    result, output = run_validator(tmp_path, version_source=source)

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert not marker.exists()


def test_computed_version_cannot_execute_code(tmp_path: Path) -> None:
    marker = tmp_path / "version-expression-was-executed"
    source = f"__version__ = __import__('pathlib').Path({str(marker)!r}).touch()\n"
    result, output = run_validator(tmp_path, version_source=source)

    assert result.returncode != 0
    assert "literal string assignment" in result.stderr
    assert not marker.exists()
    assert not output.exists()


@pytest.mark.parametrize("source", [None, "this is not valid python !!!\n"])
def test_dev_skips_application_version_and_packaging(tmp_path: Path, source: str | None) -> None:
    result, output = run_validator(
        tmp_path,
        ref="refs/heads/dev",
        version_source=source,
        without_site_packages=True,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text() == "image_tag=dev\nis_stable=false\nchannel=test\n"
