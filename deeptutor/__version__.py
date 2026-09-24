"""Source-package version and Docker runtime version resolution.

``__version__`` remains the fallback for source and Python-package installs.
Tagged Docker builds inject ``DEEPTUTOR_APP_VERSION`` so the running image,
CLI banner, and web UI report the Git tag that produced the image.
"""

from __future__ import annotations

import os

__version__ = "1.6.13"


def get_runtime_version() -> str:
    """Return the build-injected Docker version or the source fallback."""

    return os.getenv("DEEPTUTOR_APP_VERSION", "").strip() or __version__


__all__ = ("__version__", "get_runtime_version")
