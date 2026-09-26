"""Release versions stay fixed while interrupted dual-registry pushes recover."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import runpy
import subprocess
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "publish_image_manifests.py"
IMAGES = ("docker.cnb.cool/johnnliu/deeptutor", "ghcr.io/malphitee/deeptutor")
DIGESTS = {"amd64": "sha256:" + "a" * 64, "arm64": "sha256:" + "b" * 64}
INDEX_DIGEST = "sha256:" + "c" * 64


def _index(digest=INDEX_DIGEST):
    return {
        "digest": digest,
        "manifests": [
            {"platform": {"os": "linux", "architecture": arch}, "digest": child}
            for arch, child in DIGESTS.items()
        ],
    }


def _created_tags(command):
    return [command[index + 1] for index, value in enumerate(command) if value == "--tag"]


@pytest.fixture
def publication(tmp_path, monkeypatch):
    def setup(image_tag="1.2.3"):
        directory = tmp_path / "digests"
        for arch, digest in DIGESTS.items():
            artifact = directory / f"image-digests-{arch}" / f"{arch}.digest"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(digest + "\n")
        suffixes = [image_tag]
        if "-" not in image_tag:
            suffixes.append("latest")
        tags = [f"{image}:{suffix}" for image in IMAGES for suffix in suffixes]
        environment = {
            "CNB_IMAGE": IMAGES[0],
            "GHCR_IMAGE": IMAGES[1],
            "DIGEST_DIR": str(directory),
            "METADATA_JSON": json.dumps({"tags": tags}),
            "PUBLICATION_CHANNEL": "production",
            "IMAGE_TAG": image_tag,
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
        for name, value in environment.items():
            monkeypatch.setenv(name, value)

        calls = []
        manifests = {}
        behavior = {}

        def seed(image, manifest=None):
            manifest = deepcopy(manifest if manifest is not None else _index())
            manifests[f"{image}:{image_tag}"] = manifest
            manifests[f"{image}@{manifest['digest']}"] = manifest

        def docker(command, **kwargs):
            calls.append(command)
            assert command[:3] == ["docker", "buildx", "imagetools"]
            assert kwargs == {"check": True, "capture_output": True, "text": True}
            if command[3] == "inspect":
                reference = command[4]
                if command[-1] == "{{json .Image}}":
                    arch = next(
                        arch for arch, digest in DIGESTS.items() if reference.endswith(digest)
                    )
                    result = {"os": "linux", "architecture": arch}
                elif reference in manifests:
                    result = manifests[reference]
                else:
                    message = behavior.get("missing_error", f"ERROR: {reference}: not found\n")
                    raise subprocess.CalledProcessError(1, command, stderr=message)
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result))

            assert command[3] == "create"
            created_tags = _created_tags(command)
            if behavior.get("fail_tag") in created_tags:
                raise subprocess.CalledProcessError(1, command, stderr="registry upload failed")
            image = created_tags[0].rpartition(":")[0]
            sources = command[4 + len(created_tags) * 2 :]
            if len(sources) == 2:
                assert sources == [f"{image}@{digest}" for digest in DIGESTS.values()]
                result = _index()
                if behavior.get("different_new_index") and image == IMAGES[1]:
                    result["digest"] = "sha256:" + "d" * 64
                if behavior.get("wrong_new_child") and image == IMAGES[1]:
                    result["manifests"][0]["digest"] = "sha256:" + "e" * 64
            else:
                assert len(sources) == 1
                assert sources[0].startswith(image + "@")
                result = deepcopy(manifests[sources[0]])
            for tag in created_tags:
                manifests[tag] = deepcopy(result)
            manifests[f"{image}@{result['digest']}"] = deepcopy(result)
            return subprocess.CompletedProcess(command, 0, stdout="")

        monkeypatch.setattr(subprocess, "run", docker)
        main = runpy.run_path(str(SCRIPT))["main"]
        return SimpleNamespace(
            main=main,
            seed=seed,
            calls=calls,
            manifests=manifests,
            behavior=behavior,
            tags=tags,
            image_tag=image_tag,
            summary=tmp_path / "summary.md",
        )

    return setup


def test_new_release_verifies_both_versions_before_copying_aliases(publication):
    state = publication()
    state.main()
    creates = [call for call in state.calls if call[3] == "create"]
    assert [_created_tags(call) for call in creates[:2]] == [[f"{image}:1.2.3"] for image in IMAGES]
    first_alias = state.calls.index(creates[2])
    last_version_push = state.calls.index(creates[1])
    verified = state.calls[last_version_push + 1 : first_alias]
    assert [call[4] for call in verified] == [f"{image}:1.2.3" for image in IMAGES]
    for image, command in zip(IMAGES, creates[2:], strict=True):
        assert _created_tags(command) == [f"{image}:latest"]
        assert command[-1] == f"{image}@{INDEX_DIGEST}"
    assert INDEX_DIGEST in state.summary.read_text()


def test_existing_same_release_is_not_written_again(publication):
    state = publication()
    for image in IMAGES:
        state.seed(image)
    state.main()
    creates = [call for call in state.calls if call[3] == "create"]
    assert len(creates) == 2
    assert all(f"{image}:1.2.3" not in _created_tags(call) for call in creates for image in IMAGES)
    assert all(call[-1].endswith("@" + INDEX_DIGEST) for call in creates)


@pytest.mark.parametrize("damage", ["rebuilt_child", "different_index", "missing_platform"])
def test_existing_different_release_stops_before_any_write(publication, damage):
    state = publication()
    state.seed(IMAGES[0])
    invalid = _index()
    if damage == "rebuilt_child":
        invalid["manifests"][0]["digest"] = "sha256:" + "e" * 64
    elif damage == "different_index":
        invalid["digest"] = "sha256:" + "d" * 64
    else:
        invalid["manifests"].pop()
    state.seed(IMAGES[1], invalid)
    with pytest.raises(SystemExit):
        state.main()
    assert not any(call[3] == "create" for call in state.calls)
    assert not state.summary.exists()


def test_missing_first_registry_does_not_write_before_checking_second(publication):
    state = publication()
    invalid = _index()
    invalid["manifests"][0]["digest"] = "sha256:" + "e" * 64
    state.seed(IMAGES[1], invalid)
    with pytest.raises(SystemExit):
        state.main()
    assert not any(call[3] == "create" for call in state.calls)


def test_partial_version_push_retry_only_creates_missing_version(publication, capsys):
    state = publication()
    state.behavior["fail_tag"] = f"{IMAGES[1]}:1.2.3"
    with pytest.raises(subprocess.CalledProcessError):
        state.main()
    assert "registry upload failed" in capsys.readouterr().err
    assert f"{IMAGES[0]}:1.2.3" in state.manifests
    assert f"{IMAGES[1]}:1.2.3" not in state.manifests
    assert not any(tag.endswith(":latest") for tag in state.manifests)
    assert not state.summary.exists()

    state.behavior.clear()
    state.calls.clear()
    state.main()
    creates = [call for call in state.calls if call[3] == "create"]
    assert len(creates) == 3
    assert _created_tags(creates[0]) == [f"{IMAGES[1]}:1.2.3"]
    assert all(f"{IMAGES[0]}:1.2.3" not in _created_tags(call) for call in creates)
    assert all(state.manifests[tag]["digest"] == INDEX_DIGEST for tag in state.tags)


@pytest.mark.parametrize("damage", ["different_new_index", "wrong_new_child"])
def test_inconsistent_new_versions_do_not_move_aliases(publication, damage):
    state = publication()
    state.behavior[damage] = True
    with pytest.raises(SystemExit):
        state.main()
    creates = [call for call in state.calls if call[3] == "create"]
    assert [_created_tags(call) for call in creates] == [[f"{image}:1.2.3"] for image in IMAGES]
    assert not state.summary.exists()


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: unauthorized: authentication required",
        "ERROR: failed to do request: connection reset by peer",
        "ERROR: dial tcp: lookup registry: host not found",
        "ERROR: unexpected status from HEAD request: 503 Service Unavailable",
        "ERROR: ghcr.io/unrelated/image:1.2.3: not found",
        "ERROR: not found",
    ],
)
def test_registry_query_errors_are_not_treated_as_unused_versions(publication, capsys, message):
    state = publication()
    state.behavior["missing_error"] = message
    with pytest.raises(subprocess.CalledProcessError):
        state.main()
    assert message in capsys.readouterr().err
    assert not any(call[3] == "create" for call in state.calls)


def test_explicit_manifest_unknown_allows_first_publication(publication):
    state = publication()
    state.behavior["missing_error"] = (
        'ERROR: {"code":"MANIFEST_UNKNOWN","message":"manifest unknown"}'
    )
    state.main()
    assert state.summary.exists()


def test_prerelease_version_uses_same_protection_without_latest(publication):
    state = publication("1.2.3-rc.1")
    state.seed(IMAGES[0])
    state.main()
    creates = [call for call in state.calls if call[3] == "create"]
    assert _created_tags(creates[0]) == [f"{IMAGES[1]}:1.2.3-rc.1"]
    assert all(not tag.endswith(":latest") for call in creates for tag in _created_tags(call))


def test_existing_prerelease_is_verified_without_any_tag_writes(publication):
    state = publication("1.2.3-rc.1")
    for image in IMAGES:
        state.seed(image)
    state.main()
    assert not any(call[3] == "create" for call in state.calls)
    assert state.summary.exists()


def test_alias_push_failure_retry_keeps_both_version_tags(publication):
    state = publication()
    state.behavior["fail_tag"] = f"{IMAGES[1]}:latest"
    with pytest.raises(subprocess.CalledProcessError):
        state.main()
    versions = {f"{image}:1.2.3": deepcopy(state.manifests[f"{image}:1.2.3"]) for image in IMAGES}
    assert f"{IMAGES[0]}:latest" in state.manifests
    assert f"{IMAGES[1]}:latest" not in state.manifests
    assert not state.summary.exists()

    state.behavior.clear()
    state.calls.clear()
    state.main()
    creates = [call for call in state.calls if call[3] == "create"]
    assert [_created_tags(call) for call in creates] == [[f"{image}:latest"] for image in IMAGES]
    assert all(state.manifests[tag] == manifest for tag, manifest in versions.items())
