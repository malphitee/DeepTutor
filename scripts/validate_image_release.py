#!/usr/bin/env python3
"""Validate Docker publication events and their version before emitting CI outputs."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
import sys

_NUMBER = r"(?:0|[1-9][0-9]*)"
_RELEASE_TAG = re.compile(
    rf"v(?P<version>{_NUMBER}\.{_NUMBER}\.{_NUMBER}"
    rf"(?P<prerelease>-(?:alpha|beta|rc)\.{_NUMBER})?)"
)


def parse_release_tag(tag: str) -> tuple[str, bool]:
    """Return the canonical image version and stability shared by publish workflows."""
    match = _RELEASE_TAG.fullmatch(tag)
    if match is None:
        raise ValueError(
            f"Invalid release tag {tag!r}; expected vX.Y.Z or "
            "vX.Y.Z-(alpha|beta|rc).N without leading zeroes."
        )
    return match.group("version"), match.group("prerelease") is None


def validate_publication(environment: Mapping[str, str]) -> dict[str, str]:
    """Return safe workflow outputs derived from one canonical version tag."""
    event = environment.get("GITHUB_EVENT_NAME", "")
    ref = environment.get("GITHUB_REF", "")
    if event != "push" or environment.get("REF_DELETED", "false").lower() != "false":
        raise ValueError(
            f"Unsupported publication event {event!r} for {ref!r}: require an undeleted push."
        )
    if not ref.startswith("refs/tags/"):
        raise ValueError(f"Only a version tag can publish images, got {ref!r}.")
    tag = ref.removeprefix("refs/tags/")
    image_tag, is_stable = parse_release_tag(tag)
    return {
        "image_tag": image_tag,
        "is_stable": str(is_stable).lower(),
        "channel": "production",
    }


def main() -> int:
    try:
        values = validate_publication(os.environ)
        output_path = os.environ.get("GITHUB_OUTPUT")
        if not output_path:
            raise ValueError("GITHUB_OUTPUT must be set to write validated publication outputs.")
        output = "".join(f"{key}={value}\n" for key, value in values.items())
        with Path(output_path).open("a", encoding="utf-8") as file:
            file.write(output)
    except (ValueError, OSError, ImportError) as error:
        print(f"Image release validation failed: {error}", file=sys.stderr)
        return 1
    print(f"Validated {values['channel']} image tag: {values['image_tag']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
