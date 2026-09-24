"""Single source of truth for the DeepTutor version.

To cut a release, bump ``__version__`` here and commit before tagging.
Stable tags use ``v1.4.0``; prerelease tags use ``v1.4.0-rc.1`` while the
Python version can be ``1.4.0rc1`` (likewise alpha/a and beta/b). CI checks
normalized versions before publishing images or PyPI packages. The web
sidebar badge and CLI banner read from this file directly.
"""

__version__ = "1.6.12"

__all__ = ("__version__",)
