"""Contract tests for release-tag gating in publish workflows."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from packaging.version import InvalidVersion, Version
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
    if publication == "docker":
        return (REPOSITORY_ROOT / "scripts/validate_image_release.py").read_text()
    document, _ = _workflow(publication)
    validator = document["jobs"]["validate-release-tag"]
    return next(step["run"] for step in validator["steps"] if step.get("shell") == "python")


def _run_validator(
    publication: str,
    tag: str,
    tmp_path: Path,
    *,
    event: str | None = None,
    ref: str | None = None,
    deleted: bool = False,
) -> subprocess.CompletedProcess[str]:
    output = tmp_path / f"{publication}-output.txt"
    if publication == "docker":
        repository = tmp_path / "repository"
        (repository / "scripts").mkdir(parents=True, exist_ok=True)
        (repository / "deeptutor").mkdir(exist_ok=True)
        script_path = repository / "scripts/validate_image_release.py"
        script_path.write_text(_validator_script(publication))
        try:
            version = str(Version(tag.removeprefix("v")))
        except InvalidVersion:
            version = "1.2.3"
        (repository / "deeptutor/__version__.py").write_text(f"__version__ = {version!r}\n")
        command = [sys.executable, str(script_path)]
    else:
        # Actions executes shell: python from a temporary script outside the checkout.
        script_path = tmp_path / "pypi-guard.py"
        script_path.write_text(_validator_script(publication))
        command = [sys.executable, str(script_path)]
    return subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env={
            "GITHUB_EVENT_NAME": event or ("push" if publication == "docker" else "release"),
            "GITHUB_REF": ref or f"refs/tags/{tag}",
            "REF_DELETED": str(deleted).lower(),
            "RELEASE_TAG": tag,
            "GITHUB_OUTPUT": str(output),
            "PATH": os.environ["PATH"],
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("publication", RELEASE_WORKFLOWS)
def test_publication_events_are_guarded(publication: str) -> None:
    document, publish_job_name = _workflow(publication)
    validator = document["jobs"]["validate-release-tag"]

    if publication == "docker":
        assert validator["if"] == (
            "github.repository == 'malphitee/DeepTutor' && "
            "github.event_name == 'push' && !github.event.deleted && "
            "(github.ref == 'refs/heads/dev' || startsWith(github.ref, 'refs/tags/v'))"
        )
    else:
        assert validator["if"] == "startsWith(github.event.release.tag_name, 'v')"
    assert document["jobs"][publish_job_name]["needs"] == "validate-release-tag"


@pytest.mark.parametrize(
    ("publication", "tag"),
    [
        ("docker", "v1.2.3"),
        ("docker", "v1.2.3-beta.2"),
        ("docker", "v1.2.3-rc.1"),
        ("docker", "v1.2.3-alpha.1"),
        ("pypi", "v1.2.3"),
        ("pypi", "v1.2.3-rc.1"),
        ("pypi", "v1.2.3-alpha.1"),
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
        ("docker", "v1.2.3rc1"),
        ("docker", "v1.2.3+build.1"),
        ("docker", "v1.2.3-rc.01"),
        ("pypi", "vmain"),
        ("pypi", "v1.2"),
        ("pypi", "v1.2.x"),
        ("pypi", "v01.2.3"),
        ("pypi", "v1.2.3+"),
        ("pypi", "v1.2.3...."),
        ("pypi", "v1.2.3rc1"),
        ("pypi", "v1.2.3+build.1"),
        ("pypi", "v1.2.3-rc.01"),
    ],
)
def test_malformed_version_tags_fail_the_guard(publication: str, tag: str, tmp_path: Path) -> None:
    result = _run_validator(publication, tag, tmp_path)

    assert result.returncode != 0
    assert repr(tag) in result.stderr


def test_docker_uses_validated_tag_and_stable_latest_only(tmp_path: Path) -> None:
    result = _run_validator("docker", "v1.2.3-rc.1", tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "docker-output.txt").read_text() == (
        "image_tag=1.2.3-rc.1\nis_stable=false\nchannel=production\n"
    )

    document, _ = _workflow("docker")
    metadata = next(
        step for step in document["jobs"]["build-and-push"]["steps"] if step.get("id") == "meta"
    )
    tags = metadata["with"]["tags"]
    assert "needs.validate-release-tag.outputs.image_tag" in tags
    assert "needs.validate-release-tag.outputs.is_stable == 'true'" in tags
    assert "needs.validate-release-tag.outputs.channel == 'production'" in tags
    assert metadata["with"]["flavor"] == "latest=false"


def test_dev_push_selects_test_channel_without_latest(tmp_path: Path):
    result = _run_validator("docker", "", tmp_path, ref="refs/heads/dev")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "docker-output.txt").read_text() == (
        "image_tag=dev\nis_stable=false\nchannel=test\n"
    )


@pytest.mark.parametrize(
    ("tag", "image_tag", "stable"),
    [
        ("v0.0.0", "0.0.0", True),
        ("v1.2.3", "1.2.3", True),
        ("v1.2.3-alpha.1", "1.2.3-alpha.1", False),
        ("v1.2.3-beta.2", "1.2.3-beta.2", False),
        ("v1.2.3-rc.1", "1.2.3-rc.1", False),
    ],
)
def test_version_push_selects_production_and_only_plain_versions_are_stable(
    tmp_path: Path, tag: str, image_tag: str, stable: bool
):
    result = _run_validator("docker", tag, tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "docker-output.txt").read_text() == (
        f"image_tag={image_tag}\nis_stable={str(stable).lower()}\nchannel=production\n"
    )


@pytest.mark.parametrize(
    ("event", "ref", "deleted"),
    [
        ("push", "refs/heads/main", False),
        ("push", "refs/heads/feature/user-isolation", False),
        ("push", "refs/heads/dev-copy", False),
        ("push", "refs/tags/dev", False),
        ("push", "refs/tags/1.2.3", False),
        ("push", "refs/heads/dev", True),
        ("push", "refs/tags/v1.2.3", True),
        ("release", "refs/tags/v1.2.3", False),
        ("workflow_dispatch", "refs/heads/dev", False),
        ("workflow_dispatch", "refs/tags/v1.2.3", False),
        ("pull_request", "refs/heads/dev", False),
    ],
)
def test_docker_rejects_other_events_refs_and_deletions(tmp_path, event, ref, deleted):
    result = _run_validator("docker", "", tmp_path, event=event, ref=ref, deleted=deleted)
    assert result.returncode != 0
    assert not (tmp_path / "docker-output.txt").exists()


def test_docker_publishes_one_build_to_both_owned_registries():
    document, publish_job_name = _workflow("docker")
    # PyYAML follows YAML 1.1, where the Actions key `on` is parsed as True.
    triggers = document[True]
    assert triggers == {"push": {"branches": ["dev"], "tags": ["v*"]}}
    assert document["permissions"]["packages"] == "write"
    assert document["concurrency"]["cancel-in-progress"] is False
    assert document["concurrency"]["group"] == (
        "docker-images-${{ startsWith(github.ref, 'refs/tags/') && 'production' || 'dev' }}"
    )
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
    assert metadata["tags"].splitlines() == [
        "type=raw,value=${{ needs.validate-release-tag.outputs.image_tag }}",
        "type=sha,format=short,prefix=dev-,enable=${{ "
        "needs.validate-release-tag.outputs.channel == 'test' }}",
        "type=raw,value=latest,enable=${{ "
        "needs.validate-release-tag.outputs.channel == 'production' && "
        "needs.validate-release-tag.outputs.is_stable == 'true' }}",
    ]
    assert document["env"]["DOCKER_METADATA_SHORT_SHA_LENGTH"] == "12"
    builders = [
        step["with"]
        for step in steps
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(builders) == 1
    assert builders[0]["target"] == "production"
    assert builders[0]["platforms"] == "${{ matrix.platform }}"
    assert "tags" not in builders[0]
    assert builders[0]["outputs"] == (
        'type=image,"name=${{ env.GHCR_IMAGE }},${{ env.CNB_IMAGE }}",'
        "push-by-digest=true,name-canonical=true,push=true"
    )
    assert builders[0]["provenance"] is False
    assert builders[0]["sbom"] is False


def test_native_architecture_builds_only_publish_tags_after_both_succeed():
    document, _ = _workflow("docker")
    build = document["jobs"]["build-and-push"]
    assert build["runs-on"] == "${{ matrix.runner }}"
    assert build["strategy"]["fail-fast"] is False
    assert build["strategy"]["matrix"]["include"] == [
        {"arch": "amd64", "platform": "linux/amd64", "runner": "ubuntu-24.04"},
        {"arch": "arm64", "platform": "linux/arm64", "runner": "ubuntu-24.04-arm"},
    ]
    assert not any("setup-qemu" in step.get("uses", "") for step in build["steps"])
    builder = next(step["with"] for step in build["steps"] if step.get("id") == "build")
    upload = next(
        step["with"]
        for step in build["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["name"] == "image-digests-${{ matrix.arch }}"
    assert upload["if-no-files-found"] == "error"
    assert upload["overwrite"] is True
    assert upload["retention-days"] == 7
    publish = document["jobs"]["publish-manifest"]
    assert publish["needs"] == ["validate-release-tag", "build-and-push"]
    assert "if" not in publish  # Do not publish a partial build using always().
    build_metadata = next(step["with"] for step in build["steps"] if step.get("id") == "meta")
    publish_metadata = next(step["with"] for step in publish["steps"] if step.get("id") == "meta")
    assert publish_metadata == build_metadata
    publisher = next(step for step in publish["steps"] if step.get("id") == "publish")
    assert publisher["run"] == "python scripts/publish_image_manifests.py"
    assert publisher["env"]["PUBLICATION_CHANNEL"] == (
        "${{ needs.validate-release-tag.outputs.channel }}"
    )
    assert publisher["env"]["IMAGE_TAG"] == "${{ needs.validate-release-tag.outputs.image_tag }}"


def test_registry_cache_is_shared_across_refs_without_overwriting_other_builds():
    """New version tags must see dev's dependency cache despite GHA ref isolation."""
    document, _ = _workflow("docker")
    build = document["jobs"]["build-and-push"]
    builder = next(step["with"] for step in build["steps"] if step.get("id") == "build")
    image = document["env"]["GHCR_IMAGE"]

    def cache_entries(value: str, arch: str, channel: str) -> list[dict[str, str]]:
        replacements = {
            "${{ env.GHCR_IMAGE }}": image,
            "${{ matrix.arch }}": arch,
            "${{ needs.validate-release-tag.outputs.channel }}": channel,
        }
        for expression, resolved in replacements.items():
            value = value.replace(expression, resolved)
        assert "${{" not in value
        return [dict(part.split("=", 1) for part in line.split(",")) for line in value.splitlines()]

    writers: dict[tuple[str, str], str] = {}
    readers: dict[tuple[str, str], set[str]] = {}
    for platform in build["strategy"]["matrix"]["include"]:
        arch = platform["arch"]
        for channel in ("test", "production"):
            exports = cache_entries(builder["cache-to"], arch, channel)
            assert len(exports) == 1
            export = exports[0]
            assert export["type"] == "registry"
            assert export["mode"] == "max"
            assert export["ignore-error"] == "true"
            assert export["image-manifest"] == export["oci-mediatypes"] == "true"
            assert export["ref"].startswith(f"{image}:buildcache-")
            writers[(arch, channel)] = export["ref"]

            imports = cache_entries(builder["cache-from"], arch, channel)
            readers[(arch, channel)] = {
                entry["ref"] for entry in imports if entry["type"] == "registry"
            }
            assert {entry["scope"] for entry in imports if entry["type"] == "gha"} == {
                f"deeptutor-{arch}"
            }

    assert len(set(writers.values())) == len(writers) == 4
    for (arch, channel), sources in readers.items():
        assert sources == {writers[(arch, "test")], writers[(arch, "production")]}
        assert writers[(arch, channel)] in sources
    assert "ignore-error" not in builder["outputs"]


def test_docker_validates_checked_out_application_version_before_building():
    document, _ = _workflow("docker")
    steps = document["jobs"]["validate-release-tag"]["steps"]
    checkout_index = next(
        i for i, step in enumerate(steps) if step.get("uses", "").startswith("actions/checkout@")
    )
    validate_index = next(i for i, step in enumerate(steps) if step.get("id") == "validate")
    assert checkout_index < validate_index
    assert steps[validate_index]["run"] == "python scripts/validate_image_release.py"
    parser = next(step for step in steps if step.get("name") == "Install version parser")
    assert parser["if"] == "startsWith(github.ref, 'refs/tags/')"
    assert parser["run"] == "python -m pip install packaging==25.0"


def _publication_fixture(tmp_path: Path, monkeypatch):
    """Exercise the actual publication script with a simulated Docker registry."""
    images = ("ghcr.io/malphitee/deeptutor", "docker.cnb.cool/johnnliu/deeptutor")
    digests = {"amd64": "sha256:" + "a" * 64, "arm64": "sha256:" + "b" * 64}
    directory = tmp_path / "digests"
    for arch, digest in digests.items():
        artifact = directory / f"image-digests-{arch}"
        artifact.mkdir(parents=True)
        (artifact / f"{arch}.digest").write_text(digest + "\n")
    tags = [f"{image}:{tag}" for image in images for tag in ("dev", "dev-0123456789ab")]
    monkeypatch.setenv("GHCR_IMAGE", images[0])
    monkeypatch.setenv("CNB_IMAGE", images[1])
    monkeypatch.setenv("DIGEST_DIR", str(directory))
    monkeypatch.setenv("METADATA_JSON", json.dumps({"tags": tags}))
    monkeypatch.setenv("PUBLICATION_CHANNEL", "test")
    monkeypatch.setenv("IMAGE_TAG", "dev")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    calls = []
    behavior = {}

    def docker(command, **kwargs):
        calls.append(command)
        assert command[:3] == ["docker", "buildx", "imagetools"]
        if command[3] == "create":
            if behavior.get("push_failure") and images[1] in command[5]:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)
        assert command[3] == "inspect"
        reference = command[4]
        if "@" in reference:
            arch = next(arch for arch, digest in digests.items() if reference.endswith(digest))
            result = {"os": "linux", "architecture": behavior.get("source_arch", arch)}
        else:
            entries = [
                {"platform": {"os": "linux", "architecture": arch}, "digest": digest}
                for arch, digest in digests.items()
            ]
            if behavior.get("missing_platform"):
                entries.pop()
            if behavior.get("wrong_child"):
                entries[0]["digest"] = "sha256:" + "e" * 64
            suffix = (
                "d" if behavior.get("different_digest") and reference.startswith(images[1]) else "c"
            )
            result = {"digest": "sha256:" + suffix * 64, "manifests": entries}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result))

    monkeypatch.setattr(subprocess, "run", docker)
    script = (REPOSITORY_ROOT / "scripts" / "publish_image_manifests.py").read_text()
    return script, directory, images, digests, tags, calls, behavior


def test_manifest_publication_combines_same_digests_in_both_registries(tmp_path, monkeypatch):
    script, _, images, digests, tags, calls, _ = _publication_fixture(tmp_path, monkeypatch)
    exec(compile(script, "publish-manifests", "exec"), {"__name__": "__main__"})
    creates = [call for call in calls if call[3] == "create"]
    assert len(creates) == 2
    assert all(call[3] == "inspect" for call in calls[:4])
    for image, command in zip(images, creates, strict=True):
        assert command[-2:] == [f"{image}@{digest}" for digest in digests.values()]
        assert [command[i + 1] for i, value in enumerate(command) if value == "--tag"] == [
            tag for tag in tags if tag.startswith(image + ":")
        ]
    assert "sha256:" + "c" * 64 in (tmp_path / "summary.md").read_text()


@pytest.mark.parametrize("damage", ["missing", "extra", "malformed", "duplicate"])
def test_manifest_publication_rejects_invalid_digest_artifacts(tmp_path, monkeypatch, damage):
    script, directory, _, digests, _, calls, _ = _publication_fixture(tmp_path, monkeypatch)
    target = directory / "image-digests-arm64/arm64.digest"
    if damage == "missing":
        target.unlink()
    elif damage == "extra":
        (directory / "unexpected.digest").write_text(digests["amd64"])
    elif damage == "malformed":
        target.write_text("sha256:not-a-digest")
    else:
        target.write_text(digests["amd64"])
    with pytest.raises(SystemExit):
        exec(compile(script, "publish-manifests", "exec"), {"__name__": "__main__"})
    assert calls == []


@pytest.mark.parametrize("damage", ["foreign_repository", "unequal_tags", "invalid_tag"])
def test_manifest_publication_rejects_unexpected_tags(tmp_path, monkeypatch, damage):
    script, _, _, _, tags, calls, _ = _publication_fixture(tmp_path, monkeypatch)
    if damage == "foreign_repository":
        tags.append("ghcr.io/unrelated/image:latest")
    elif damage == "unequal_tags":
        tags.pop()
    else:
        tags[0] += ";unexpected"
    monkeypatch.setenv("METADATA_JSON", json.dumps({"tags": tags}))
    with pytest.raises(SystemExit):
        exec(compile(script, "publish-manifests", "exec"), {"__name__": "__main__"})
    assert calls == []


def test_manifest_publication_rejects_wrong_source_arch_before_tagging(tmp_path, monkeypatch):
    script, _, _, _, _, calls, behavior = _publication_fixture(tmp_path, monkeypatch)
    behavior["source_arch"] = "riscv64"
    with pytest.raises(SystemExit, match="Unexpected platform"):
        exec(compile(script, "publish-manifests", "exec"), {"__name__": "__main__"})
    assert not any(call[3] == "create" for call in calls)


@pytest.mark.parametrize("damage", ["missing_platform", "wrong_child", "different_digest"])
def test_manifest_publication_fails_if_registry_result_is_inconsistent(
    tmp_path, monkeypatch, damage
):
    script, _, _, _, _, _, behavior = _publication_fixture(tmp_path, monkeypatch)
    behavior[damage] = True
    with pytest.raises(SystemExit):
        exec(compile(script, "publish-manifests", "exec"), {"__name__": "__main__"})
    assert not (tmp_path / "summary.md").exists()


def test_manifest_publication_does_not_hide_one_registry_push_failure(tmp_path, monkeypatch):
    script, _, _, _, _, _, behavior = _publication_fixture(tmp_path, monkeypatch)
    behavior["push_failure"] = True
    with pytest.raises(subprocess.CalledProcessError):
        exec(compile(script, "publish-manifests", "exec"), {"__name__": "__main__"})
    assert not (tmp_path / "summary.md").exists()
