"""Execution capability gates for the single-instance multi-user mode.

Manim is intentionally handled separately from the normal ``exec`` tool.  It
launches a generated Python program directly with ``subprocess.Popen`` from a
capability renderer, so filtering the tool list alone cannot protect ordinary
accounts.  Keep this policy in one small module and call it at both the turn
boundary and the renderer boundary.
"""

from __future__ import annotations

from .context import get_current_user

MANIM_RENDER_TYPES = frozenset({"manim_video", "manim_image"})


def assert_capability_execution_allowed(
    capability: str,
    *,
    render_mode: str | None = None,
) -> None:
    """Reject direct code execution that is unsafe for ordinary accounts.

    Administrators retain the existing local/single-user behavior.  Ordinary
    users may still use the non-Manim visualization renderers; only the
    capability paths that generate and execute Python code are denied.
    """

    user = get_current_user()
    if user.is_admin:
        return

    name = str(capability or "").strip().lower()
    # The facade normally canonicalizes CLI aliases, but WebSocket/CLI
    # adapters can call the turn service directly.  Keep the security decision
    # canonical even when an adapter has not performed that convenience step.
    if name in {"animate", "math-animator"}:
        name = "math_animator"
    elif name in {"viz", "visualization"}:
        name = "visualize"
    mode = str(render_mode or "").strip().lower()
    if name == "math_animator" or (name == "visualize" and mode in MANIM_RENDER_TYPES):
        raise PermissionError(
            "This account cannot run Manim code. Ask an administrator to use an "
            "isolated execution service."
        )


def assert_manim_execution_allowed() -> None:
    """Defence-in-depth guard immediately before a Manim subprocess."""

    user = get_current_user()
    if not user.is_admin:
        raise PermissionError(
            "This account cannot run Manim code. Ask an administrator to use an "
            "isolated execution service."
        )


def assert_local_subagent_execution_allowed() -> None:
    """Reject local agent CLIs when the caller is not the administrator.

    Local subagent backends are child processes of the app and inherit its
    filesystem, environment, and credentials.  Their per-CLI workspace flags
    are not an OS isolation boundary, so an ordinary account must not launch
    one until a private runner contract exists for that deployment.
    """

    user = get_current_user()
    if not user.is_admin:
        raise PermissionError(
            "This account cannot run a local subagent. Ask an administrator to use an "
            "isolated execution service."
        )


def assert_sandbox_execution_allowed() -> None:
    """Reject an exec/CLI-app call before it can touch a work directory.

    The deployment runner currently has the administrator compatibility
    workspace mounted into one shared container.  A SYSTEM isolation label
    therefore does not make its work directory, temporary files, or artifacts
    private per account.  Partner runtimes are explicit extensions of the local
    administrator and retain their existing owner-controlled execution policy;
    real non-admin accounts are denied until a private runner contract exists.
    """

    user = get_current_user()
    if user.is_admin:
        return

    from deeptutor.services.partners.scope import is_partner_user_id

    if is_partner_user_id(user.id):
        return
    raise PermissionError(
        "This account cannot run code. Ask an administrator to use an isolated "
        "execution service."
    )


__all__ = [
    "MANIM_RENDER_TYPES",
    "assert_capability_execution_allowed",
    "assert_local_subagent_execution_allowed",
    "assert_manim_execution_allowed",
    "assert_sandbox_execution_allowed",
]
