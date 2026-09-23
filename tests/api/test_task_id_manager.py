from datetime import datetime, timedelta

from deeptutor.api.utils.task_id_manager import TaskIDManager


def _manager() -> TaskIDManager:
    manager = TaskIDManager()
    manager._task_ids = {}
    manager._task_metadata = {}
    return manager


def test_generate_prunes_expired_completed_tasks() -> None:
    manager = _manager()
    old_id = manager.generate_task_id("kb", "old")
    manager.update_task_status(old_id, "completed")
    manager._task_metadata[old_id]["finished_at"] = (
        datetime.now() - timedelta(hours=25)
    ).isoformat()

    manager.generate_task_id("kb", "new")

    assert old_id not in manager._task_metadata
    assert "old" not in manager._task_ids


def test_completed_task_metadata_has_hard_count_bound() -> None:
    manager = _manager()
    manager._MAX_COMPLETED_TASKS = 3

    for index in range(8):
        task_id = manager.generate_task_id("kb", f"task-{index}")
        manager.update_task_status(task_id, "completed")

    manager.cleanup_old_tasks()

    assert len(manager._task_metadata) == 3
    assert len(manager._task_ids) == 3


def test_task_keys_are_partitioned_by_owner_and_scope() -> None:
    manager = _manager()

    alice = manager.generate_task_id(
        "kb", "same-key", owner_id="u_alice", scope_key="/data/users/u_alice"
    )
    bob = manager.generate_task_id(
        "kb", "same-key", owner_id="u_bob", scope_key="/data/users/u_bob"
    )

    assert alice != bob
    assert manager.task_belongs_to(alice, "u_alice", scope_key="/data/users/u_alice")
    assert not manager.task_belongs_to(alice, "u_bob", scope_key="/data/users/u_bob")
    assert not manager.task_belongs_to(alice, "u_alice", scope_key="/data/users/u_bob")


def test_legacy_task_without_owner_metadata_is_not_visible_to_ordinary_user() -> None:
    manager = _manager()
    task_id = manager.generate_task_id("kb", "legacy")

    assert not manager.task_belongs_to(task_id, "u_alice", scope_key="/data/users/u_alice")
