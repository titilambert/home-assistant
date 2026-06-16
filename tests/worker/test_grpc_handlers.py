"""Tests for homeassistant.core_grpc.services.state_service."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from homeassistant.core_grpc.protos import core_pb2
from homeassistant.core_grpc.services.state_service import (
    DATA_ENTITY_ENTRY,
    CoreServiceServicer,
)


@pytest.fixture
def hass_mock() -> MagicMock:
    """Return a minimal hass mock for servicer tests."""
    hass = MagicMock()
    hass.data = {}
    hass.states.async_set = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.services.async_register = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services._services = {}
    hass.bus.async_listen = MagicMock(return_value=lambda: None)
    hass.config_entries.async_get_entry = MagicMock(return_value=None)
    return hass


@pytest.fixture
def servicer(hass_mock: MagicMock) -> CoreServiceServicer:
    """Return a CoreServiceServicer backed by hass_mock."""
    return CoreServiceServicer(hass_mock)


async def test_set_state_basic(
    servicer: CoreServiceServicer, hass_mock: MagicMock
) -> None:
    """SetState updates hass.states."""
    request = core_pb2.SetStateRequest(
        entity_id="sensor.test",
        state="42",
        attributes={"unit": "°C"},
        entry_id="",
    )
    context = MagicMock()
    response = await servicer.SetState(request, context)
    assert response.success is True
    hass_mock.states.async_set.assert_called_once()


async def test_set_state_records_entry_id(
    servicer: CoreServiceServicer, hass_mock: MagicMock
) -> None:
    """SetState records entity_id → entry_id mapping."""
    request = core_pb2.SetStateRequest(
        entity_id="sensor.test",
        state="on",
        entry_id="entry123",
    )
    await servicer.SetState(request, MagicMock())
    assert hass_mock.data.get(DATA_ENTITY_ENTRY, {}).get("sensor.test") == "entry123"


async def test_get_state_not_found(
    servicer: CoreServiceServicer, hass_mock: MagicMock
) -> None:
    """GetState returns found=False for unknown entity."""
    hass_mock.states.get.return_value = None
    request = core_pb2.GetStateRequest(entity_id="sensor.unknown")
    response = await servicer.GetState(request, MagicMock())
    assert response.found is False


async def test_get_config(servicer: CoreServiceServicer, hass_mock: MagicMock) -> None:
    """GetConfig returns hass.config values."""
    hass_mock.config.time_zone = "America/Toronto"
    hass_mock.config.language = "fr"
    hass_mock.config.latitude = 45.5
    hass_mock.config.longitude = -73.6
    hass_mock.config.country = "CA"
    hass_mock.config.currency = "CAD"
    units = MagicMock()
    units.temperature_unit = "°C"
    units.length_unit = "km"
    units.mass_unit = "kg"
    units.pressure_unit = "hPa"
    units.volume_unit = "L"
    units.wind_speed_unit = "km/h"
    units.accumulated_precipitation_unit = "mm"
    hass_mock.config.units = units

    request = core_pb2.GetConfigRequest()
    response = await servicer.GetConfig(request, MagicMock())
    assert response.time_zone == "America/Toronto"
    assert response.language == "fr"
    assert response.temperature_unit == "°C"
    assert abs(response.latitude - 45.5) < 0.001


async def test_get_entry_not_found(
    servicer: CoreServiceServicer, hass_mock: MagicMock
) -> None:
    """GetEntry returns found=False for unknown entry."""
    hass_mock.config_entries.async_get_entry.return_value = None
    request = core_pb2.GetEntryRequest(entry_id="nonexistent")
    response = await servicer.GetEntry(request, MagicMock())
    assert response.found is False


async def test_get_entry_found(
    servicer: CoreServiceServicer, hass_mock: MagicMock
) -> None:
    """GetEntry returns entry data for known entry."""
    entry = MagicMock()
    entry.domain = "pi_hole"
    entry.title = "Pi-hole"
    entry.data = {"host": "192.168.1.1", "api_key": "secret"}
    entry.options = {}
    hass_mock.config_entries.async_get_entry.return_value = entry
    hass_mock.config.config_dir = "/tmp"

    request = core_pb2.GetEntryRequest(entry_id="abc123")
    response = await servicer.GetEntry(request, MagicMock())
    assert response.found is True
    assert response.domain == "pi_hole"
    assert response.title == "Pi-hole"
    config_data = json.loads(response.config)
    assert config_data["host"] == "192.168.1.1"
