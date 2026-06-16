"""Tests for homeassistant.worker.registry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.worker.const import WORKER_STATUS_RUNNING
from homeassistant.worker.registry import WorkerRegistry


@pytest.fixture
def hass_mock():
    """Return a minimal hass mock."""
    hass = MagicMock()
    hass.data = {}
    hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *a: f(*a))
    return hass


@pytest.fixture
def process_conf():
    return {
        "name": "Test Worker",
        "type": "process",
        "port": 50052,
        "core_address": "localhost:50051",
    }


@pytest.fixture
def remote_conf():
    return {
        "name": "Remote Worker",
        "type": "remote",
        "address": "192.168.1.10:50052",
        "core_address": "localhost:50051",
    }


def test_registry_builds_process_worker(hass_mock, process_conf):
    """WorkerRegistry creates a ProcessWorker for type=process."""
    registry = WorkerRegistry(hass_mock, [process_conf])
    worker = registry.get_worker("Test Worker")
    assert worker is not None
    assert worker.worker_type == "process"
    assert worker.name == "Test Worker"


def test_registry_builds_remote_worker(hass_mock, remote_conf):
    """WorkerRegistry creates a RemoteWorker for type=remote."""
    registry = WorkerRegistry(hass_mock, [remote_conf])
    worker = registry.get_worker("Remote Worker")
    assert worker is not None
    assert worker.worker_type == "remote"


def test_registry_unknown_type_skipped(hass_mock):
    """Unknown worker type is skipped with an error log."""
    conf = {"name": "Bad", "type": "unknown", "port": 50052}
    registry = WorkerRegistry(hass_mock, [conf])
    assert registry.get_worker("Bad") is None


def test_get_available_workers_filters_running(hass_mock, process_conf, remote_conf):
    """get_available_workers returns only RUNNING workers with capacity."""
    registry = WorkerRegistry(hass_mock, [process_conf, remote_conf])
    # Initially all unavailable
    assert registry.get_available_workers() == []
    # Mark one as running
    registry.get_worker("Test Worker")._status = WORKER_STATUS_RUNNING
    available = registry.get_available_workers()
    assert len(available) == 1
    assert available[0].name == "Test Worker"


def test_get_available_workers_respects_capacity(hass_mock, process_conf):
    """get_available_workers excludes workers at capacity."""
    conf = {**process_conf, "max_integrations": 1}
    registry = WorkerRegistry(hass_mock, [conf])
    worker = registry.get_worker("Test Worker")
    worker._status = WORKER_STATUS_RUNNING
    worker._active_integrations = 1  # at capacity
    assert registry.get_available_workers() == []


def test_all_workers_returns_all(hass_mock, process_conf, remote_conf):
    """all_workers returns all declared workers regardless of status."""
    registry = WorkerRegistry(hass_mock, [process_conf, remote_conf])
    assert len(registry.all_workers()) == 2


@pytest.mark.asyncio
async def test_async_reload_waiting_entries(hass_mock, process_conf):
    """async_reload_waiting_entries reloads only remote entries in SETUP_RETRY for this worker."""
    from homeassistant.config_entries import ConfigEntryState

    registry = WorkerRegistry(hass_mock, [process_conf])
    worker = registry.get_worker("Test Worker")
    worker._status = WORKER_STATUS_RUNNING

    # Mock entry in SETUP_RETRY assigned to this worker
    entry_ok = MagicMock()
    entry_ok.data = {"runtime_mode": "remote", "worker_name": "Test Worker"}
    entry_ok.state = ConfigEntryState.SETUP_RETRY
    entry_ok.entry_id = "abc"

    # Mock entry in SETUP_RETRY assigned to different worker
    entry_other = MagicMock()
    entry_other.data = {"runtime_mode": "remote", "worker_name": "Other Worker"}
    entry_other.state = ConfigEntryState.SETUP_RETRY

    # Mock local entry (should not be reloaded)
    entry_local = MagicMock()
    entry_local.data = {"runtime_mode": "local"}
    entry_local.state = ConfigEntryState.SETUP_RETRY

    hass_mock.config_entries.async_entries.return_value = [
        entry_ok,
        entry_other,
        entry_local,
    ]
    hass_mock.config_entries.async_reload = MagicMock()
    hass_mock.async_create_task = MagicMock()

    await worker.async_reload_waiting_entries()

    # Only the matching entry should be scheduled for reload
    assert hass_mock.async_create_task.call_count == 1
