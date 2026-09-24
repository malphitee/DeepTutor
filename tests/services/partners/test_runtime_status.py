from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deeptutor.services.partners.runtime_status import PartnerRuntimeStatusRepository


def test_runtime_status_is_shared_and_does_not_persist_channel_credentials(tmp_path) -> None:
    path = tmp_path / "status.sqlite3"
    writer = PartnerRuntimeStatusRepository(path)
    reader = PartnerRuntimeStatusRepository(path)

    written = writer.set(
        "ada",
        owner_id="worker-a",
        running=True,
        state="running",
        payload={
            "partner_id": "ada",
            "name": "Ada",
            "channels": {"telegram": {"token": "secret"}},
        },
        started_at="2026-09-01T12:00:00",
    )

    assert "channels" not in written
    status = reader.get("ada")
    assert status is not None
    assert status["running"] is True
    assert status["runtime_owner_id"] == "worker-a"
    assert status["name"] == "Ada"
    assert "channels" not in status

    writer.delete("ada")
    assert reader.get("ada") is None


def test_shared_channel_runtime_keeps_only_the_public_status_fields(tmp_path) -> None:
    writer = PartnerRuntimeStatusRepository(tmp_path / "status.sqlite3")
    writer.set(
        "ada",
        owner_id="worker-a",
        running=True,
        state="running",
        payload={
            "channels": {"whatsapp": {"bridge_token": "secret"}},
            "channel_runtime": {
                "whatsapp": {
                    "enabled": True,
                    "running": True,
                    "bridge_token": "secret",
                    "setup_updated_at": 123.0,
                    "setup": {
                        "status": "connected",
                        "message": "Ready",
                        "qr_payload": "scan-me",
                        "token": "secret",
                    },
                }
            },
        },
    )

    status = PartnerRuntimeStatusRepository(writer.path).get("ada")
    assert status["channel_runtime"]["whatsapp"] == {
        "enabled": True,
        "running": True,
        "setup_updated_at": 123.0,
        "setup": {"status": "connected", "message": "Ready", "qr_payload": "scan-me"},
    }
    assert "secret" not in str(status)


def test_heartbeat_does_not_acknowledge_a_new_lifecycle_command(tmp_path, monkeypatch) -> None:
    from deeptutor.services.partners import runtime_status

    clock = [100.0]
    monkeypatch.setattr(runtime_status.time, "time", lambda: clock[0])
    repository = PartnerRuntimeStatusRepository(tmp_path / "status.sqlite3")
    repository.set("ada", owner_id="worker-a", running=True, state="running")
    clock[0] = 110.0
    status = repository.set(
        "ada", owner_id="worker-a", running=True, state="running", heartbeat=True
    )

    assert status["runtime_updated_at"] == 110.0
    assert status["runtime_control_updated_at"] == 100.0


def test_control_timeout_cannot_extend_leader_liveness_but_heartbeat_can(
    tmp_path, monkeypatch
) -> None:
    from deeptutor.services.partners import runtime_status

    clock = [100.0]
    monkeypatch.setattr(runtime_status.time, "time", lambda: clock[0])
    repository = PartnerRuntimeStatusRepository(tmp_path / "status.sqlite3")
    previous = repository.set("ada", owner_id="leader", running=True, state="running")
    clock[0] = 120.0
    failure = repository.set(
        "ada",
        owner_id="leader",
        running=True,
        state="control_failed",
        payload=previous,
        last_reload_error="Partner leader did not process the command. Retry the channel.",
    )
    assert failure["runtime_updated_at"] == 100.0
    assert failure["runtime_control_updated_at"] == 120.0

    clock[0] = 125.0
    heartbeat = repository.set(
        "ada", owner_id="leader", running=True, state="running", heartbeat=True
    )
    assert heartbeat["runtime_updated_at"] == 125.0
    assert heartbeat["runtime_control_updated_at"] == 120.0
    assert heartbeat["runtime_state"] == "control_failed"


def test_stale_channel_start_keeps_its_age_across_status_reads(monkeypatch) -> None:
    from deeptutor.services.partners import manager as manager_module
    from deeptutor.services.partners.manager import PartnerConfig, PartnerInstance, PartnerManager

    clock = [100.0]
    monkeypatch.setattr(manager_module.time, "time", lambda: clock[0])
    state = {"whatsapp": {"enabled": True, "running": True, "setup": {"status": "connecting"}}}
    instance = PartnerInstance(
        "ada",
        PartnerConfig(name="Ada", channels={"whatsapp": {"enabled": True}}),
        tasks=[SimpleNamespace(done=lambda: False, get_name=lambda: "partner:ada:runner")],
        channel_manager=SimpleNamespace(get_status=lambda: state),
    )
    manager = PartnerManager()
    assert manager.channel_runtime_status(instance)["whatsapp"]["setup"]["status"] == "connecting"
    clock[0] = 161.0
    timed_out = manager.channel_runtime_status(instance)["whatsapp"]
    assert timed_out["setup_updated_at"] == 100.0
    assert timed_out["setup"]["error_code"] == "channel_start_timeout"
    assert manager.channel_runtime_status(instance)["whatsapp"] == timed_out

    state["whatsapp"]["setup"]["status"] = "connected"
    assert manager.channel_runtime_status(instance)["whatsapp"]["setup"]["status"] == "connected"


@pytest.mark.asyncio
async def test_manager_publishes_channel_changes_and_stops_the_publisher(
    partners_root, monkeypatch
) -> None:
    from deeptutor.services.partners import manager as manager_module
    from deeptutor.services.partners import runtime_status
    from deeptutor.services.partners.manager import PartnerConfig, PartnerManager

    repository = PartnerRuntimeStatusRepository(partners_root / "status.sqlite3")
    monkeypatch.setattr(runtime_status, "_repository", repository)
    monkeypatch.setattr(manager_module, "_RUNTIME_STATUS_INTERVAL_SECONDS", 0.001)

    class Runner:
        def __init__(self, _partner_id, _config, bus, **_kwargs):
            self.bus = bus

        async def run(self):
            await asyncio.Event().wait()

    async def router(*_args):
        await asyncio.Event().wait()

    state = {"whatsapp": {"enabled": True, "running": True, "setup": {"status": "connecting"}}}

    async def stop_all():
        return None

    manager = PartnerManager()
    monkeypatch.setattr(manager_module, "PartnerRunner", Runner)
    monkeypatch.setattr(manager, "_outbound_router", router)
    monkeypatch.setattr(
        manager,
        "_build_channel_manager",
        lambda *_a, **_k: SimpleNamespace(channels={}, get_status=lambda: state, stop_all=stop_all),
    )
    instance = await manager.start_partner(
        "ada", PartnerConfig(name="Ada", channels={"whatsapp": {"enabled": True}})
    )
    publisher = instance.runtime_status_task
    try:
        state["whatsapp"]["setup"]["status"] = "connected"
        for _ in range(20):
            await asyncio.sleep(0.002)
            saved_channel = repository.get("ada")["channel_runtime"]["whatsapp"]
            if saved_channel["setup"]["status"] == "connected":
                break
        assert saved_channel["setup"]["status"] == "connected"
        repository.set(
            "ada",
            owner_id="other-worker",
            running=True,
            state="control_failed",
            last_reload_error="Previous command timed out.",
        )
        # An idempotent start acknowledges recovery even if the leader already
        # has this Partner, instead of leaving the follower waiting forever.
        assert await manager.start_partner("ada") is instance
        assert repository.get("ada")["runtime_state"] == "running"
        # The status publisher alone must not keep a dead Partner marked alive.
        original_tasks = instance.tasks
        instance.tasks = []
        assert instance.running is False
        instance.tasks = original_tasks
    finally:
        await manager.stop_partner("ada")
    assert publisher.done()
    assert repository.get("ada")["running"] is False


@pytest.mark.asyncio
async def test_start_failure_is_persisted_without_exception_secrets(
    partners_root, monkeypatch
) -> None:
    from deeptutor.services.partners import runtime_status
    from deeptutor.services.partners.manager import PartnerManager

    repository = PartnerRuntimeStatusRepository(partners_root / "status.sqlite3")
    monkeypatch.setattr(runtime_status, "_repository", repository)
    manager = PartnerManager()

    def fail(_partner_id):
        raise ValueError("secret-token")

    monkeypatch.setattr(manager, "_ensure_partner_dirs", fail)
    with pytest.raises(ValueError):
        await manager.start_partner("ada")
    status = repository.get("ada")
    assert status["runtime_state"] == "start_failed"
    assert status["last_reload_error"] == "Partner startup failed (ValueError)."
    assert "secret-token" not in str(status)


@pytest.mark.asyncio
async def test_old_publisher_cannot_overwrite_a_replacement_instance(monkeypatch) -> None:
    from deeptutor.services.partners import manager as manager_module
    from deeptutor.services.partners.manager import PartnerConfig, PartnerInstance, PartnerManager

    manager = PartnerManager()
    instance = PartnerInstance("ada", PartnerConfig(name="Ada"))
    manager._partners["ada"] = instance
    published = []
    monkeypatch.setattr(manager_module, "_RUNTIME_STATUS_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(manager, "_publish_runtime_status", lambda *a, **kw: published.append(kw))
    publisher = asyncio.create_task(manager._publish_runtime_status_loop(instance))
    await asyncio.sleep(0)
    manager._partners["ada"] = PartnerInstance("ada", PartnerConfig(name="Replacement"))

    await asyncio.wait_for(publisher, timeout=1)
    assert published == []


def test_dead_local_instance_does_not_report_connected_channels() -> None:
    from deeptutor.services.partners.manager import PartnerConfig, PartnerInstance, PartnerManager

    state = {"whatsapp": {"enabled": True, "running": True, "setup": {"status": "connected"}}}
    instance = PartnerInstance(
        "ada",
        PartnerConfig(name="Ada", channels={"whatsapp": {"enabled": True}}),
        channel_manager=SimpleNamespace(get_status=lambda: state),
    )
    channel = PartnerManager().channel_runtime_status(instance)["whatsapp"]
    assert channel["running"] is False
    assert channel["setup"]["status"] == "disconnected"


def test_dead_channel_task_reports_error_while_partner_stays_running() -> None:
    from deeptutor.services.partners.manager import PartnerConfig, PartnerInstance, PartnerManager

    state = {"whatsapp": {"enabled": True, "running": True, "setup": {"status": "connected"}}}
    instance = PartnerInstance(
        "ada",
        PartnerConfig(name="Ada", channels={"whatsapp": {"enabled": True}}),
        tasks=[
            SimpleNamespace(done=lambda: False, get_name=lambda: "partner:ada:runner"),
            SimpleNamespace(done=lambda: True, get_name=lambda: "partner:ada:ch:whatsapp"),
        ],
        channel_manager=SimpleNamespace(get_status=lambda: state),
    )
    channel = PartnerManager().channel_runtime_status(instance)["whatsapp"]
    assert instance.running is True
    assert channel["running"] is False
    assert channel["setup"]["error_code"] == "channel_listener_stopped"
