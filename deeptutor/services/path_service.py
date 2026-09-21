#!/usr/bin/env python
"""
PathService - centralized runtime storage layout for ``data/user``.

Runtime data is constrained to:

data/user/
├── .runtime/
├── chat_history.db
├── logs/
├── settings/
└── workspace/
    ├── memory/
    ├── notebook/
    ├── co-writer/
    ├── book/
    └── chat/
        ├── chat/
        ├── deep_solve/
        ├── deep_question/
        ├── deep_research/
        ├── math_animator/
        └── _detached_exec/
"""

from pathlib import Path
import shutil
from typing import Literal, cast

from deeptutor.runtime.home import PACKAGE_ROOT, get_runtime_data_root
from deeptutor.utils.secret_files import ensure_private_directory, write_secret_text

AgentModule = Literal[
    "solve",
    "chat",
    "question",
    "research",
    "co-writer",
    "exec_workspace",
    "logs",
    "math_animator",
]

ChatWorkspaceFeature = Literal[
    "chat",
    "deep_solve",
    "deep_question",
    "deep_research",
    "math_animator",
    "_detached_exec",
]

WorkspaceFeature = Literal[
    "memory",
    "notebook",
    "co-writer",
    "chat",
    "book",
    "reading",
    "timed_media",
]


class PathService:
    """Runtime path manager rooted at a workspace root.

    The default root is the historical ``data/`` directory.  The optional
    multi-user layer instantiates this class with ``data/users/<uid>/`` so the
    public API can stay the same while disk writes become scoped per user.
    """

    _instance: "PathService | None" = None

    _AGENT_TO_WORKSPACE: dict[str, tuple[str, str | None]] = {
        "solve": ("chat", "deep_solve"),
        "chat": ("chat", "chat"),
        "question": ("chat", "deep_question"),
        "research": ("chat", "deep_research"),
        "math_animator": ("chat", "math_animator"),
        "co-writer": ("co-writer", None),
        "exec_workspace": ("chat", "_detached_exec"),
    }
    _PRIVATE_SUFFIXES = {".json", ".sqlite", ".db", ".md", ".yaml", ".yml", ".py", ".log"}

    def __init__(self, workspace_root: Path | None = None):
        self._package_root = PACKAGE_ROOT
        self._uses_default_workspace_root = workspace_root is None
        if workspace_root is not None and workspace_root.is_symlink():
            raise PermissionError("Workspace root cannot be a symbolic link")
        self._workspace_root = (workspace_root or get_runtime_data_root()).resolve()
        self._project_root = self._workspace_root.parent.resolve()
        self._user_data_dir = self._workspace_root / "user"

    @classmethod
    def get_instance(cls) -> "PathService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def user_data_dir(self) -> Path:
        return self.get_user_root()

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def package_root(self) -> Path:
        return self._package_root

    def get_user_root(self) -> Path:
        return self.scoped_path(self._user_data_dir, "user data root")

    def get_knowledge_bases_root(self) -> Path:
        return self.scoped_path(self._workspace_root / "knowledge_bases", "knowledge-base root")

    def get_parse_cache_root(self) -> Path:
        """Shared, content-addressed document-parse cache.

        Lives under the workspace root (sibling of ``knowledge_bases``) so it is
        automatically scoped per user/workspace. Both knowledge-base indexing
        and question extraction draw from this one cache, keyed by
        ``(source_hash, parser_signature)`` — see ``deeptutor/services/parsing``.
        """
        return self.scoped_path(self._workspace_root / "parse_cache", "parse-cache root")

    def get_chat_history_db(self) -> Path:
        return self._safe_child(self.get_user_root(), "chat_history.db", "chat history")

    def get_public_outputs_root(self) -> Path:
        # Public artifact downloads are an authenticated resource boundary.
        # Keep the same component-by-component symlink checks as every other
        # per-user path; resolving this raw path first would let a later
        # ``user`` directory symlink redirect downloads into another scope.
        return self.scoped_path(self._user_data_dir, "public output root")

    def resolve_public_output_path(self, path: str | Path) -> Path | None:
        """Return a safe, public output file below this service's user root.

        Resolving and authorizing the path in one operation gives callers the
        exact canonical path they may read.  In particular, callers should not
        validate against one workspace and then reconstruct the file path from
        a different root.
        """
        raw_candidate = Path(path)
        raw_root = self.get_public_outputs_root()
        if not raw_candidate.is_absolute():
            raw_candidate = raw_root / raw_candidate

        # Check the lexical path before resolving it.  Otherwise a symlinked
        # ``workspace``/``chat`` component can redirect an otherwise valid
        # output URL into another user's tree, and the subsequent
        # ``relative_to(root.resolve())`` check would incorrectly accept it.
        if not self._uses_default_workspace_root:
            try:
                relative_raw = raw_candidate.relative_to(raw_root)
            except ValueError:
                return None
            cursor = raw_root
            for part in relative_raw.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    return None

        candidate = raw_candidate.resolve()
        root = raw_root.resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None

        if not candidate.is_file():
            return None
        if candidate.suffix.lower() in self._PRIVATE_SUFFIXES:
            return None

        parts = relative.parts
        if parts[:3] == ("workspace", "co-writer", "audio"):
            return candidate

        if (
            len(parts) >= 5
            and parts[:3] == ("workspace", "chat", "deep_solve")
            and "artifacts" in parts[4:]
        ):
            return candidate

        if (
            len(parts) >= 5
            and parts[:3] == ("workspace", "chat", "math_animator")
            and "artifacts" in parts[4:]
        ):
            return candidate

        if len(parts) >= 5 and parts[:2] == ("workspace", "chat") and "code_runs" in parts[3:]:
            return candidate

        # Generated media (imagegen / videogen tools write under <task>/media/).
        if len(parts) >= 5 and parts[:2] == ("workspace", "chat") and "media" in parts[3:]:
            return candidate

        if len(parts) >= 5 and parts[:3] == ("workspace", "chat", "chat") and parts[4] == "exec":
            return candidate

        # Files a CLI app produced. One directory per turn shared by every app,
        # not one per app, so a model can render with one and post-process with
        # another. Listed explicitly rather than folded into the ``exec`` branch:
        # what is publicly linkable is worth being able to read off this function.
        if len(parts) >= 5 and parts[:3] == ("workspace", "chat", "chat") and parts[4] == "cli":
            return candidate

        if len(parts) >= 4 and parts[:3] == ("workspace", "chat", "_detached_exec"):
            return candidate

        return None

    def is_public_output_path(self, path: str | Path) -> bool:
        return self.resolve_public_output_path(path) is not None

    def get_workspace_dir(self) -> Path:
        return self.scoped_path(self._user_data_dir / "workspace", "workspace root")

    def get_settings_dir(self) -> Path:
        return self.scoped_path(self._user_data_dir / "settings", "settings root")

    def get_runtime_state_dir(self) -> Path:
        """Private state for active runs, never part of a content workspace."""

        return self.scoped_path(self._user_data_dir / ".runtime", "runtime state root")

    def get_settings_file(self, name: str) -> Path:
        name = self._safe_component(name, "settings file")
        if "." not in name:
            name = f"{name}.json"
        return self._safe_child(self.get_settings_dir(), name, "settings file")

    def get_runtime_config_file(self, name: str) -> Path:
        name = self._safe_component(name, "runtime config")
        if not name.endswith(".yaml"):
            name = f"{name}.yaml"
        return self._safe_child(self.get_settings_dir(), name, "runtime config")

    def get_workspace_feature_dir(self, feature: WorkspaceFeature) -> Path:
        return self.scoped_path(self.get_workspace_dir() / feature, f"{feature} workspace root")

    def get_chat_workspace_root(self) -> Path:
        return self.get_workspace_feature_dir("chat")

    def get_chat_feature_dir(self, feature: ChatWorkspaceFeature) -> Path:
        return self.scoped_path(
            self.get_chat_workspace_root() / feature,
            f"{feature} chat workspace root",
        )

    def get_task_workspace(self, feature: str, task_id: str) -> Path:
        task_root = self._resolve_feature_root(feature)
        return self._safe_child(task_root, task_id, "task")

    def get_session_workspace(self, feature: str, session_id: str) -> Path:
        session_root = self._resolve_feature_root(feature)
        return self._safe_child(session_root, session_id, "session")

    def _resolve_feature_root(self, feature: str) -> Path:
        if feature in {
            "chat",
            "deep_solve",
            "deep_question",
            "deep_research",
            "math_animator",
            "_detached_exec",
        }:
            return self.get_chat_feature_dir(cast(ChatWorkspaceFeature, feature))
        if feature in {"memory", "notebook", "co-writer", "book"}:
            return self.get_workspace_feature_dir(cast(WorkspaceFeature, feature))
        raise ValueError(f"Unknown workspace feature: {feature}")

    def get_agent_base_dir(self) -> Path:
        return self.get_workspace_dir()

    def get_agent_dir(self, module: str) -> Path:
        if module == "logs":
            return self.get_logs_dir()
        root_name, child_name = self._AGENT_TO_WORKSPACE[module]
        base = self.get_workspace_feature_dir(cast(WorkspaceFeature, root_name))
        if not child_name:
            return base
        if root_name == "chat":
            return self.get_chat_feature_dir(cast(ChatWorkspaceFeature, child_name))
        return self.scoped_path(base / child_name, f"{module} agent root")

    def get_session_file(self, module: str) -> Path:
        return self._safe_child(self.get_agent_dir(module), "sessions.json", "session store")

    def get_task_dir(self, module: str, task_id: str) -> Path:
        return self._safe_child(self.get_agent_dir(module), task_id, "task")

    def get_notebook_dir(self) -> Path:
        return self.get_workspace_feature_dir("notebook")

    def get_notebook_file(self, notebook_id: str) -> Path:
        self._safe_component(notebook_id, "notebook")
        return self._safe_child(self.get_notebook_dir(), f"{notebook_id}.json", "notebook")

    def get_notebook_index_file(self) -> Path:
        return self._safe_child(self.get_notebook_dir(), "notebooks_index.json", "notebook index")

    def get_memory_dir(self) -> Path:
        return self.scoped_path(self.workspace_root / "memory", "memory root")

    def migrate_legacy_memory_markdown(self) -> bool:
        """Move the old workspace memory files into the canonical memory root once.

        Older versions stored loose Markdown files in
        ``data/user/workspace/memory``.  Keeping this migration out of the path
        getter is important: a read-only path lookup must never recreate files
        that the v1-to-v2 migration has already archived.
        """
        new_dir = self.get_memory_dir()
        old_dir = self.get_workspace_feature_dir("memory")
        default_root = (self.project_root / "data").resolve()
        marker = old_dir / ".migrated-to-data-memory-v2"
        if self.workspace_root != default_root or marker.exists() or not old_dir.exists():
            return False

        legacy_files = sorted(
            path for path in old_dir.iterdir() if path.is_file() and path.suffix == ".md"
        )
        if not legacy_files:
            return False

        ensure_private_directory(new_dir)
        conflict_dir = new_dir / "backup" / "legacy-workspace"
        for source in legacy_files:
            target = new_dir / source.name
            if not target.exists():
                shutil.move(str(source), str(target))
                continue
            if source.read_bytes() == target.read_bytes():
                source.unlink()
                continue

            ensure_private_directory(conflict_dir)
            conflict = conflict_dir / source.name
            counter = 1
            while conflict.exists() and conflict.read_bytes() != source.read_bytes():
                conflict = conflict_dir / f"{source.stem}-{counter}{source.suffix}"
                counter += 1
            if conflict.exists():
                source.unlink()
            else:
                shutil.move(str(source), str(conflict))

        write_secret_text(
            marker,
            "Legacy workspace memory was migrated to data/memory.\n",
        )
        return True

    def get_solve_dir(self) -> Path:
        return self.get_chat_feature_dir("deep_solve")

    def get_solve_session_file(self) -> Path:
        return self.get_session_file("solve")

    def get_solve_task_dir(self, task_id: str) -> Path:
        return self.get_task_dir("solve", task_id)

    def get_chat_dir(self) -> Path:
        return self.get_chat_feature_dir("chat")

    def get_chat_session_file(self) -> Path:
        return self.get_session_file("chat")

    def get_question_dir(self) -> Path:
        return self.get_chat_feature_dir("deep_question")

    def get_question_batch_dir(self, batch_id: str) -> Path:
        return self.get_task_dir("question", batch_id)

    def get_research_dir(self) -> Path:
        return self.get_chat_feature_dir("deep_research")

    def get_research_reports_dir(self) -> Path:
        return self.scoped_path(self.get_research_dir() / "reports", "research reports root")

    def get_co_writer_dir(self) -> Path:
        return self.get_workspace_feature_dir("co-writer")

    def get_co_writer_history_file(self) -> Path:
        return self._safe_child(self.get_co_writer_dir(), "history.json", "co-writer history")

    def get_co_writer_tool_calls_dir(self) -> Path:
        return self.scoped_path(self.get_co_writer_dir() / "tool_calls", "co-writer tool calls")

    def get_co_writer_audio_dir(self) -> Path:
        return self.scoped_path(self.get_co_writer_dir() / "audio", "co-writer audio")

    def get_co_writer_docs_dir(self) -> Path:
        """Root directory holding co-writer documents (one sub-directory per doc)."""
        return self.scoped_path(self.get_co_writer_dir() / "documents", "co-writer documents")

    def get_co_writer_doc_root(self, doc_id: str) -> Path:
        """Per-document root directory."""
        self._safe_component(doc_id, "document")
        return self._safe_child(self.get_co_writer_docs_dir(), f"doc_{doc_id}", "document")

    def get_co_writer_doc_manifest(self, doc_id: str) -> Path:
        return self._safe_child(
            self.get_co_writer_doc_root(doc_id), "manifest.json", "document manifest"
        )

    # ── Book Engine paths ────────────────────────────────────────────────

    def get_book_dir(self) -> Path:
        """Root directory holding all books (one sub-directory per book)."""
        return self.get_workspace_feature_dir("book")

    def get_book_root(self, book_id: str) -> Path:
        """Per-book root directory."""
        self._safe_component(book_id, "book")
        return self._safe_child(self.get_book_dir(), f"book_{book_id}", "book")

    def get_book_manifest_file(self, book_id: str) -> Path:
        return self._safe_child(self.get_book_root(book_id), "manifest.json", "book manifest")

    def get_book_spine_file(self, book_id: str) -> Path:
        return self._safe_child(self.get_book_root(book_id), "spine.json", "book spine")

    def get_book_progress_file(self, book_id: str) -> Path:
        return self._safe_child(self.get_book_root(book_id), "progress.json", "book progress")

    def get_book_inputs_file(self, book_id: str) -> Path:
        return self._safe_child(self.get_book_root(book_id), "inputs.json", "book inputs")

    def get_book_log_file(self, book_id: str) -> Path:
        return self._safe_child(self.get_book_root(book_id), "log.md", "book log")

    def get_book_pages_dir(self, book_id: str) -> Path:
        return self.scoped_path(self.get_book_root(book_id) / "pages", "book pages")

    def get_book_page_file(self, book_id: str, page_id: str) -> Path:
        self._safe_component(page_id, "book page")
        return self._safe_child(
            self.get_book_pages_dir(book_id), f"{page_id}.json", "book page"
        )

    def get_book_learning_captures_file(self, book_id: str) -> Path:
        return self._safe_child(
            self.get_book_root(book_id), "learning_captures.json", "book learning captures"
        )

    def get_book_assets_dir(self, book_id: str) -> Path:
        return self.scoped_path(self.get_book_root(book_id) / "assets", "book assets")

    def ensure_book_root(self, book_id: str) -> Path:
        root = self.get_book_root(book_id)
        root.mkdir(parents=True, exist_ok=True)
        (root / "pages").mkdir(parents=True, exist_ok=True)
        (root / "assets").mkdir(parents=True, exist_ok=True)
        return root

    def get_exec_workspace_dir(self) -> Path:
        return self.get_chat_feature_dir("_detached_exec")

    def get_logs_dir(self) -> Path:
        return self.scoped_path(self.get_user_root() / "logs", "logs root")

    def scoped_path(self, path: Path, label: str) -> Path:
        """Reject symlinked user workspace components before they are used.

        The historical admin workspace permits operator-managed links for
        compatibility. Per-user and synthetic scopes are resolved through this
        helper so a link such as ``workspace/reading -> ../another-user`` can
        never turn a scoped service into a shared-resource reader.
        """
        if self._uses_default_workspace_root:
            return path
        root = self._workspace_root
        if root.is_symlink():
            raise PermissionError("Workspace root cannot be a symbolic link")
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"{label.capitalize()} leaves the workspace root") from exc
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise PermissionError(f"{label.capitalize()} cannot be a symbolic link")
        return path

    @staticmethod
    def _safe_component(value: str, label: str) -> str:
        """Validate an untrusted identifier before using it as a path name."""

        text = str(value or "")
        if (
            not text
            or text in {".", ".."}
            or Path(text).is_absolute()
            or Path(text).name != text
            or "/" in text
            or "\\" in text
            or "\x00" in text
        ):
            raise ValueError(f"Invalid {label} path component")
        return text

    @classmethod
    def _safe_child(cls, root: Path, value: str, label: str) -> Path:
        """Resolve an identifier below *root*, rejecting symlink escapes."""

        component = cls._safe_component(value, label)
        root_path = root
        if root_path.exists() and root_path.is_symlink():
            raise ValueError(f"{label.capitalize()} root cannot be a symbolic link")
        raw = root_path / component
        if raw.is_symlink():
            raise ValueError(f"{label.capitalize()} path cannot be a symbolic link")
        resolved_root = root_path.resolve()
        resolved = raw.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"{label.capitalize()} path leaves its root") from exc
        return resolved

    def ensure_agent_dir(self, module: str) -> Path:
        path = self.get_agent_dir(module)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_task_dir(self, module: str, task_id: str) -> Path:
        path = self.get_task_dir(module, task_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_workspace_dir(self) -> Path:
        path = self.get_workspace_dir()
        return ensure_private_directory(path)

    def ensure_notebook_dir(self) -> Path:
        path = self.get_notebook_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_memory_dir(self) -> Path:
        self.migrate_legacy_memory_markdown()
        path = self.get_memory_dir()
        return ensure_private_directory(path)

    def ensure_settings_dir(self) -> Path:
        path = self.get_settings_dir()
        return ensure_private_directory(path)

    def ensure_runtime_state_dir(self) -> Path:
        return ensure_private_directory(self.get_runtime_state_dir())

    def ensure_all_directories(self) -> None:
        ensure_private_directory(self.get_user_root())
        # Validate every known user-facing workspace branch before creating
        # any of them.  A request may reach a feature lazily (reading and the
        # file library are common examples), so checking only the directories
        # this startup path happens to create would leave a symlinked branch
        # available to a later resource router.
        for feature in (
            "memory",
            "notebook",
            "co-writer",
            "book",
            "reading",
            "timed_media",
            "library",
            "learning",
            "courses",
            "suggestions",
            "personas",
            "skills",
        ):
            self.scoped_path(self.get_workspace_dir() / feature, f"{feature} workspace root")
        self.ensure_settings_dir()
        self.ensure_runtime_state_dir()
        self.ensure_workspace_dir()
        self.ensure_memory_dir()
        self.ensure_notebook_dir()
        ensure_private_directory(self.get_logs_dir())
        for workspace_feature in cast(tuple[WorkspaceFeature, ...], ("co-writer", "book")):
            self.get_workspace_feature_dir(workspace_feature).mkdir(parents=True, exist_ok=True)
        for chat_feature in cast(
            tuple[ChatWorkspaceFeature, ...],
            (
                "chat",
                "deep_solve",
                "deep_question",
                "deep_research",
                "math_animator",
                "_detached_exec",
            ),
        ):
            self.get_chat_feature_dir(chat_feature).mkdir(parents=True, exist_ok=True)
        self.get_co_writer_tool_calls_dir().mkdir(parents=True, exist_ok=True)
        self.get_co_writer_audio_dir().mkdir(parents=True, exist_ok=True)
        self.get_research_reports_dir().mkdir(parents=True, exist_ok=True)


def get_path_service() -> PathService:
    from deeptutor.multi_user.paths import get_current_path_service

    # A broken scope is never authorization to use the deployment's files.
    return get_current_path_service()


__all__ = [
    "AgentModule",
    "ChatWorkspaceFeature",
    "PathService",
    "WorkspaceFeature",
    "get_path_service",
]
