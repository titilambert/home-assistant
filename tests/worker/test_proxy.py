"""Tests for homeassistant.worker.proxy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.worker.proxy import (
    _LocalDispatcher,
    _MockBus,
    _MockConfig,
    _MockDeviceRegistry,
    _MockEntityRegistry,
    _MockStore,
    _MockUnits,
    _TranslationCache,
)


def test_mock_config_defaults() -> None:
    """_MockConfig has expected default attributes."""
    config = _MockConfig()
    assert config.time_zone == "UTC"
    assert config.language == "en"
    assert config.latitude == 0.0
    assert config.longitude == 0.0
    assert config.country == ""
    assert config.currency == ""
    assert config.config_dir  # not empty
    assert isinstance(config.units, _MockUnits)


def test_mock_units_defaults() -> None:
    """_MockUnits has expected unit attributes."""
    units = _MockUnits()
    assert units.temperature_unit == "°C"
    assert units.length_unit == "km"
    assert units.mass_unit == "kg"
    assert units.pressure_unit == "hPa"


def test_mock_bus_noop() -> None:
    """_MockBus methods return without error."""
    bus = _MockBus()
    cancel = bus.async_listen("test_event", lambda e: None)
    assert callable(cancel)
    cancel2 = bus.async_listen_once("test_event", lambda e: None)
    assert callable(cancel2)
    bus.async_fire("test_event", {})  # should not raise


def test_mock_entity_registry() -> None:
    """_MockEntityRegistry returns None/empty for all reads."""
    er = _MockEntityRegistry()
    assert er.async_get_entity_id("sensor", "pi_hole", "uid123") is None
    assert er.async_get("sensor.test") is None
    assert er.async_entries_for_config_entry("entry_id") == []
    assert er.async_entries_for_device("device_id") == []


def test_mock_device_registry() -> None:
    """_MockDeviceRegistry.async_get_or_create returns a stub with an id."""
    dr = _MockDeviceRegistry()
    entry = dr.async_get_or_create(
        config_entry_id="entry1",
        identifiers={("pi_hole", "myhost")},
        name="Pi-hole",
    )
    assert entry.id  # not empty
    assert entry.area_id is None
    # Same identifiers → same id (stable UUID)
    entry2 = dr.async_get_or_create(
        config_entry_id="entry1",
        identifiers={("pi_hole", "myhost")},
    )
    assert entry.id == entry2.id


def test_local_dispatcher() -> None:
    """_LocalDispatcher connect/send works correctly."""
    dispatcher = _LocalDispatcher()
    received: list[str] = []

    cancel = dispatcher.connect("my_signal", lambda data: received.append(data))
    dispatcher.send("my_signal", "hello")
    assert received == ["hello"]

    # After cancel, no more calls
    cancel()
    dispatcher.send("my_signal", "world")
    assert received == ["hello"]


def test_local_dispatcher_unknown_signal() -> None:
    """Sending to unknown signal does not raise."""
    dispatcher = _LocalDispatcher()
    dispatcher.send("nonexistent", "data")  # should not raise


async def test_mock_store_load_save() -> None:
    """_MockStore stores data in memory."""
    store = _MockStore(MagicMock(), 1, "test_key")
    assert await store.async_load() is None
    await store.async_save({"key": "value"})
    assert await store.async_load() == {"key": "value"}


async def test_translation_cache_miss_and_hit() -> None:
    """_TranslationCache fetches once and caches."""
    cache = _TranslationCache()
    stub = MagicMock()

    # Mock GetTranslations response
    mock_response = MagicMock()
    mock_response.translations = {
        "component.pi_hole.entity.sensor.ads_blocked.name": "Ads Blocked"
    }
    stub.GetTranslations = AsyncMock(return_value=mock_response)

    result1 = await cache.async_get(stub, "en", "entity", "pi_hole")
    result2 = await cache.async_get(stub, "en", "entity", "pi_hole")

    # Should have fetched only once
    assert stub.GetTranslations.call_count == 1
    # Same result both times
    assert result1 == result2
    assert result1 == {
        "component.pi_hole.entity.sensor.ads_blocked.name": "Ads Blocked"
    }
