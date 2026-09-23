#!/usr/bin/env python3
"""Validate Docker publication events and their version before emitting CI outputs."""

from __future__ import annotations

import ast
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


def read_application_version(repository_root: Path) -> str:
    """Read one literal version assignment without importing application code."""
    version_path = repository_root / "deeptutor" / "__version__.py"
    try:
        tree = ast.parse(version_path.read_text(encoding="utf-8"), filename=str(version_path))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise ValueError(f"Cannot read application version from {version_path}: {error}") from error

    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "__version__"
        and isinstance(node.ctx, ast.Store)
    ]
    if len(assignments) != 1:
        raise ValueError(
            f"Expected exactly one __version__ definition in {version_path}; "
            f"found {len(assignments)}."
        )

    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        else:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in targets
        ):
            continue
        if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            return statement.value.value
        break
    raise ValueError(
        f"__version__ in {version_path} must be a top-level literal string assignment."
    )


def validate_publication(environment: Mapping[str, str], repository_root: Path) -> dict[str, str]:
    """Return safe workflow outputs only for a matching version tag."""
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

    application_version = read_application_version(repository_root)
    # Version-tag publication needs packaging to compare SemVer with PEP 440.
    from packaging.version import InvalidVersion, Version

    try:
        parsed_application_version = Version(application_version)
    except InvalidVersion as error:
        raise ValueError(f"Invalid application __version__: {application_version!r}.") from error
    if Version(image_tag) != parsed_application_version:
        raise ValueError(
            f"Release tag {tag!r} does not match application __version__ {application_version!r}. "
            "Update deeptutor/__version__.py before tagging the release."
        )
    return {
        "image_tag": image_tag,
        "is_stable": str(is_stable).lower(),
        "channel": "production",
    }


def main() -> int:
    try:
        values = validate_publication(os.environ, Path(__file__).resolve().parents[1])
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
