"""
Task ID Manager - Assigns unique IDs to each background task
"""

from datetime import datetime, timedelta
import logging
import threading
from typing import Optional
import uuid

logger = logging.getLogger(__name__)


class TaskIDManager:
    """Singleton class for managing task IDs"""

    _MAX_COMPLETED_TASKS = 256

    _instance: Optional["TaskIDManager"] = None
    _lock = threading.Lock()
    _task_ids: dict[str, str] = {}  # task_key -> task_id
    _task_metadata: dict[str, dict] = {}  # task_id -> metadata

    @classmethod
    def get_instance(cls) -> "TaskIDManager":
        """Get singleton instance"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @staticmethod
    def _mapping_key(task_key: str, owner_id: str = "", scope_key: str = "") -> str:
        """Partition task-key reuse by owner and resolved workspace scope."""

        if not owner_id and not scope_key:
            return task_key
        return f"{owner_id}\x1f{scope_key}\x1f{task_key}"

    def generate_task_id(
        self,
        task_type: str,
        task_key: str,
        *,
        owner_id: str = "",
        scope_key: str = "",
    ) -> str:
        """
        Generate unique ID for task

        Args:
            task_type: Task type (e.g., 'kb_init', 'kb_upload', 'question_gen', 'solve', 'research')
            task_key: Task unique identifier (e.g., knowledge base name, question ID, etc.)
            owner_id: Authenticated account that owns the task, when available.
            scope_key: Canonical workspace/resource scope that owns the task.

        Returns:
            Task ID (format: {task_type}_{timestamp}_{uuid})
        """
        self.cleanup_old_tasks()
        with self._lock:
            mapping_key = self._mapping_key(task_key, owner_id, scope_key)
            # If task already exists, return existing ID
            if mapping_key in self._task_ids:
                return self._task_ids[mapping_key]

            # Generate new ID
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            task_id = f"{task_type}_{timestamp}_{unique_id}"

            # Save mapping and metadata
            self._task_ids[mapping_key] = task_id
            self._task_metadata[task_id] = {
                "task_type": task_type,
                "task_key": task_key,
                "mapping_key": mapping_key,
                "owner_id": owner_id,
                "scope_key": scope_key,
                "created_at": datetime.now().isoformat(),
                "status": "running",
            }

            return task_id

    def get_task_id(
        self,
        task_key: str,
        *,
        owner_id: str = "",
        scope_key: str = "",
    ) -> str | None:
        """Get task ID"""
        with self._lock:
            return self._task_ids.get(self._mapping_key(task_key, owner_id, scope_key))

    def update_task_status(self, task_id: str, status: str, **kwargs):
        """Update task status"""
        with self._lock:
            if task_id in self._task_metadata:
                self._task_metadata[task_id]["status"] = status
                self._task_metadata[task_id].update(kwargs)
                if status in ["completed", "error", "cancelled"]:
                    self._task_metadata[task_id]["finished_at"] = datetime.now().isoformat()

    def get_task_metadata(self, task_id: str) -> dict | None:
        """Get task metadata"""
        with self._lock:
            return self._task_metadata.get(task_id, {}).copy()

    def task_belongs_to(
        self,
        task_id: str,
        owner_id: str,
        *,
        scope_key: str = "",
    ) -> bool:
        """Return whether a task is owned by the supplied account/scope.

        Tasks created before ownership metadata was introduced intentionally
        return ``False`` for ordinary users.  An administrator may choose to
        inspect those legacy tasks at the API boundary, where the broader
        administrative permission is explicit.
        """

        metadata = self.get_task_metadata(task_id)
        if not metadata or not owner_id:
            return False
        if str(metadata.get("owner_id") or "") != str(owner_id):
            return False
        recorded_scope = str(metadata.get("scope_key") or "")
        # An authenticated resource request always supplies its scope.  An
        # owner id without a recorded scope is legacy/incomplete metadata and
        # must not become a ticket into that user's other resources.
        if scope_key:
            return bool(recorded_scope) and recorded_scope == str(scope_key)
        return True

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """Clean up old tasks (completed tasks older than specified hours)"""
        with self._lock:
            cutoff = datetime.now() - timedelta(hours=max_age_hours)

            to_remove = []
            for task_id, metadata in self._task_metadata.items():
                if metadata.get("status") in ["completed", "error", "cancelled"]:
                    finished_at = metadata.get("finished_at")
                    if finished_at:
                        try:
                            finished_time = datetime.fromisoformat(finished_at)
                            if finished_time < cutoff:
                                to_remove.append(task_id)
                        except Exception:
                            logger.warning("Failed to parse finished_at for task %s", task_id)

            for task_id in to_remove:
                metadata = self._task_metadata.pop(task_id, {})
                mapping_key = metadata.get("mapping_key") or metadata.get("task_key")
                if mapping_key:
                    self._task_ids.pop(mapping_key, None)

            completed = sorted(
                (
                    (str(metadata.get("finished_at") or ""), task_id)
                    for task_id, metadata in self._task_metadata.items()
                    if metadata.get("status") in ["completed", "error", "cancelled"]
                )
            )
            overflow = len(completed) - self._MAX_COMPLETED_TASKS
            for _, task_id in completed[: max(0, overflow)]:
                metadata = self._task_metadata.pop(task_id, {})
                mapping_key = metadata.get("mapping_key") or metadata.get("task_key")
                if mapping_key:
                    self._task_ids.pop(mapping_key, None)
