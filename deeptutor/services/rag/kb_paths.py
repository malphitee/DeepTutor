"""Resolve the on-disk directory backing a knowledge base.

Ordinary KBs live at ``<kb_base_dir>/<kb_name>``. A *linked* KB is a pointer to
an engine index the user already built elsewhere: its ``kb_config.json`` entry
carries an ``external_path`` we resolve to instead, so retrieval reads that
folder in place — no copy, no re-index.

This is the single seam every pipeline goes through to find a KB's storage
root. Pipelines must never compute ``Path(kb_base_dir) / kb_name`` directly, or
linked KBs would resolve to a non-existent local folder and silently return no
results.
"""

from __future__ import annotations

import json
from pathlib import Path

from deeptutor.knowledge.kb_types import external_root_of

KB_CONFIG_FILENAME = "kb_config.json"


def resolve_kb_dir(kb_base_dir: str | Path, kb_name: str) -> Path:
    """Return the directory holding ``kb_name``'s index.

    For a linked KB this is the user's external folder; for every other KB it
    is the conventional ``<kb_base_dir>/<kb_name>``.
    """
    base = Path(kb_base_dir)
    external = _external_path(base, kb_name)
    if external:
        folder = Path(external).expanduser()
        # ``kb_config.json`` is user-writable data.  Validate a persisted
        # external pointer at retrieval time as well as at registration time;
        # otherwise editing the config or swapping a symlink could make a RAG
        # pipeline read another user's directory.  An assigned administrator
        # KB is already an explicit grant and intentionally keeps its admin
        # scope.
        from deeptutor.multi_user.context import get_current_user
        from deeptutor.multi_user.paths import get_admin_path_service

        user = get_current_user()
        admin_base = get_admin_path_service().get_knowledge_bases_root().resolve()
        if user.is_admin or base.resolve() == admin_base:
            return folder
        from deeptutor.services.rag.linked_kb import assert_path_allowed

        return assert_path_allowed(str(folder))
    return base / kb_name


def _external_path(base: Path, kb_name: str) -> str | None:
    """Read a KB entry's external pointer from ``kb_config.json``, if any."""
    cfg = base / KB_CONFIG_FILENAME
    if not cfg.exists():
        return None
    try:
        with open(cfg, encoding="utf-8") as handle:
            entry = json.load(handle).get("knowledge_bases", {}).get(kb_name, {})
    except Exception:
        return None
    return external_root_of(entry)


__all__ = ["resolve_kb_dir"]
