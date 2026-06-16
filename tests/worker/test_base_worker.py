"""Tests for homeassistant.worker.workers.base."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.worker.workers.remote import RemoteWorker


@pytest.fixture
def hass_mock():
    hass = MagicMock()
    hass.data = {}
    return hass


@pytest.fixture
def remote_worker(hass_mock):
    return RemoteWorker(
        hass_mock,
        {
            "name": "Test",
            "type": "remote",
            "address": "192.168.1.10:50052",
            "core_address": "localhost:50051",
        },
    )


def test_has_capacity_unlimited(remote_worker):
    """Worker without max_integrations always has capacity."""
    assert remote_worker.has_capacity is True


def test_has_capacity_with_limit(hass_mock):
    """Worker at max capacity returns False."""
    worker = RemoteWorker(
        hass_mock,
        {
            "name": "T",
            "type": "remote",
            "address": "x:50052",
            "max_integrations": 2,
            "core_address": "localhost:50051",
        },
    )
    worker._active_integrations = 2
    assert worker.has_capacity is False


def test_increment_decrement(remote_worker):
    """increment/decrement update active_integrations correctly."""
    assert remote_worker.active_integrations == 0
    remote_worker.increment_integrations()
    remote_worker.increment_integrations()
    assert remote_worker.active_integrations == 2
    remote_worker.decrement_integrations()
    assert remote_worker.active_integrations == 1
    # Should not go below 0
    remote_worker.decrement_integrations()
    remote_worker.decrement_integrations()
    assert remote_worker.active_integrations == 0


def test_to_dict(remote_worker):
    """to_dict returns expected keys."""
    d = remote_worker.to_dict()
    assert d["name"] == "Test"
    assert d["type"] == "remote"
    assert "status" in d
    assert "address" in d
    assert "active_integrations" in d
    assert "has_capacity" in d


@pytest.mark.asyncio
async def test_async_check_reachable_no_address(hass_mock):
    """Worker with no address is not reachable."""
    worker = RemoteWorker(
        hass_mock,
        {
            "name": "T",
            "type": "remote",
            "address": "",
            "core_address": "localhost:50051",
        },
    )
    assert await worker.async_check_reachable() is False
