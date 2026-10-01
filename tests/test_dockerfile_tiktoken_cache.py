"""The image ships tiktoken's vocabularies instead of fetching them at runtime."""

from __future__ import annotations

from pathlib import Path
import re

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _production_stage() -> str:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    return dockerfile.split("AS production", 1)[1].split("\nFROM ", 1)[0]


def test_production_image_bakes_every_encoding_the_code_requests() -> None:
    stage = _production_stage()
    assert "ENV TIKTOKEN_CACHE_DIR=" in stage
    prefetch = next(line for line in stage.splitlines() if "tiktoken.get_encoding" in line)
    baked = set(re.findall(r"'(\w+_base)'", prefetch))

    requested = {
        name
        for path in (REPOSITORY_ROOT / "deeptutor").rglob("*.py")
        for name in re.findall(r'get_encoding\(\s*"(\w+)"\s*\)', path.read_text(encoding="utf-8"))
    }
    # The scan must still find the call sites, or the subset check is vacuous.
    assert "cl100k_base" in requested
    assert requested <= baked
