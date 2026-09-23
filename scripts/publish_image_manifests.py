"""Publish matching multi-platform images without replacing release versions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

ARCHITECTURES = ("amd64", "arm64")
DIGEST_PATTERN = r"sha256:[0-9a-f]{64}"


def _manifest_missing(reference: str, message: str) -> bool:
    """Only an explicit missing manifest is safe to treat as an unused tag."""
    return bool(
        re.fullmatch(r"(?:ERROR:\s*)?" + re.escape(reference) + r": not found", message.strip())
        or re.search(r"\bMANIFEST_UNKNOWN\b", message)
    )


def _docker(command: list[str], *, missing_reference: str | None = None):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        message = error.stderr or ""
        if missing_reference is not None and _manifest_missing(missing_reference, message):
            return None
        # Captured registry errors otherwise disappear behind CalledProcessError.
        if message:
            print(message.rstrip(), file=sys.stderr)
        raise


def inspect(reference: str, field: str, *, allow_missing: bool = False):
    result = _docker(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{json ." + field + "}}",
        ],
        missing_reference=reference if allow_missing else None,
    )
    if result is None:
        return None
    return json.loads(result.stdout)


def create(tags: list[str], sources: list[str]) -> None:
    command = ["docker", "buildx", "imagetools", "create"]
    for tag in tags:
        command.extend(["--tag", tag])
    _docker(command + sources)


def verify_manifest(manifest: dict, reference: str, expected_platforms: dict) -> str:
    entries = manifest.get("manifests", [])
    platforms = {
        (
            entry.get("platform", {}).get("os"),
            entry.get("platform", {}).get("architecture"),
        ): entry.get("digest")
        for entry in entries
    }
    digest = manifest.get("digest", "")
    if (
        len(entries) != 2
        or platforms != expected_platforms
        or not re.fullmatch(DIGEST_PATTERN, digest)
    ):
        raise SystemExit(f"Invalid published platform manifest: {reference}")
    return digest


def publish_release(images, tags_by_image, image_tag, digests, expected_platforms) -> None:
    """Create immutable versions first; move aliases only after both are verified."""
    existing = {}
    for image in images:
        reference = f"{image}:{image_tag}"
        manifest = inspect(reference, "Manifest", allow_missing=True)
        if manifest is not None:
            # A rebuild may have different dependencies or creation timestamps.
            # Matching source code alone does not permit replacing a version.
            existing[image] = verify_manifest(manifest, reference, expected_platforms)
    if len(set(existing.values())) > 1:
        raise SystemExit("Existing release versions have different image digests")

    # Complete all preflight checks before any write. A retry reuses the original
    # digest artifacts and only fills in a version missing from one registry.
    for image in images:
        if image not in existing:
            create(
                [f"{image}:{image_tag}"],
                [f"{image}@{digests[arch]}" for arch in ARCHITECTURES],
            )

    version_digests = {
        image: verify_manifest(
            inspect(f"{image}:{image_tag}", "Manifest"),
            f"{image}:{image_tag}",
            expected_platforms,
        )
        for image in images
    }
    if len(set(version_digests.values())) != 1:
        raise SystemExit("Release versions have different image digests; aliases were not updated")

    for image in images:
        aliases = [tag for tag in tags_by_image[image] if tag != f"{image}:{image_tag}"]
        if aliases:
            # A single index source is copied exactly, retaining its digest.
            create(aliases, [f"{image}@{version_digests[image]}"])


def main() -> None:
    directory = Path(os.environ["DIGEST_DIR"])
    expected_paths = {f"image-digests-{arch}/{arch}.digest" for arch in ARCHITECTURES}
    actual_paths = {
        str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file()
    }
    if actual_paths != expected_paths:
        raise SystemExit("Expected exactly one digest artifact for amd64 and arm64")
    digests = {}
    for arch in ARCHITECTURES:
        digest = (directory / f"image-digests-{arch}/{arch}.digest").read_text().strip()
        if not re.fullmatch(DIGEST_PATTERN, digest):
            raise SystemExit(f"Invalid digest artifact for {arch}")
        digests[arch] = digest
    if len(set(digests.values())) != 2:
        raise SystemExit("Platform digests must be distinct")

    images = (os.environ["GHCR_IMAGE"], os.environ["CNB_IMAGE"])
    tags = json.loads(os.environ["METADATA_JSON"])["tags"]
    if not isinstance(tags, list) or not tags or not all(isinstance(tag, str) for tag in tags):
        raise SystemExit("Expected nonempty unique publication tags")
    if len(tags) != len(set(tags)):
        raise SystemExit("Expected nonempty unique publication tags")
    tags_by_image = {image: [] for image in images}
    for tag in tags:
        image, separator, suffix = tag.rpartition(":")
        if (
            image not in tags_by_image
            or not separator
            or not re.fullmatch(r"[\w][\w.-]{0,127}", suffix, re.ASCII)
        ):
            raise SystemExit(f"Unexpected publication destination: {tag}")
        tags_by_image[image].append(tag)
    suffixes = [{tag.rpartition(":")[2] for tag in tags_by_image[image]} for image in images]
    if not suffixes[0] or suffixes[0] != suffixes[1]:
        raise SystemExit("Both registries must receive the same tags")

    channel = os.environ["PUBLICATION_CHANNEL"]
    image_tag = os.environ["IMAGE_TAG"]
    if channel not in {"test", "production"} or image_tag not in suffixes[0]:
        raise SystemExit("Invalid publication channel or missing image tag")
    version_pattern = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    if (channel == "test" and image_tag != "dev") or (
        channel == "production"
        and not re.fullmatch(
            version_pattern + r"(?:-(?:alpha|beta|rc)\.(?:0|[1-9]\d*))?", image_tag
        )
    ):
        raise SystemExit("Invalid image tag for publication channel")

    # Both registries must have both runnable platforms before moving any tag.
    for image in images:
        for arch in ARCHITECTURES:
            config = inspect(f"{image}@{digests[arch]}", "Image")
            if config.get("os") != "linux" or config.get("architecture") != arch:
                raise SystemExit(f"Unexpected platform for {image}@{digests[arch]}")

    expected_platforms = {("linux", arch): digests[arch] for arch in ARCHITECTURES}
    if channel == "production":
        publish_release(images, tags_by_image, image_tag, digests, expected_platforms)
    else:
        for image in images:
            create(tags_by_image[image], [f"{image}@{digests[arch]}" for arch in ARCHITECTURES])

    index_digests = {
        verify_manifest(inspect(tag, "Manifest"), tag, expected_platforms) for tag in tags
    }
    if len(index_digests) != 1:
        raise SystemExit("Published tags or registries have different image digests")
    digest = index_digests.pop()
    print(f"Verified linux/amd64 and linux/arm64 in both registries: {digest}")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(f"Both registries verified at `{digest}` (linux/amd64, linux/arm64).\n\n")
        summary.writelines(f"- `{tag}`\n" for tag in tags)


if __name__ == "__main__":
    main()
