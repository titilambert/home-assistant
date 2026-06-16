"""Integration tests for horizontal scaling using a real ProcessWorker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from unittest.mock import MagicMock

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.worker.const import DATA_WORKER_REGISTRY, WORKER_STATUS_RUNNING
from homeassistant.worker.registry import WorkerRegistry

_LOGGER = logging.getLogger(__name__)

# Port range for tests — use high ports to avoid conflicts
TEST_GRPC_PORT = 50099
TEST_WORKER_PORT = 50098


async def _wait_for_worker_ready(worker, timeout: float = 30.0) -> bool:
    """Wait until a worker transitions to RUNNING status."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if worker.status == WORKER_STATUS_RUNNING:
            return True
        await asyncio.sleep(0.5)
    return False


@pytest.fixture
async def hass_with_grpc(hass: HomeAssistant):
    """Start the Core gRPC server on a test port."""
    from homeassistant.core_grpc import async_start_grpc_server  # noqa: PLC0415

    await async_start_grpc_server(hass, port=TEST_GRPC_PORT)
    yield hass
    # Cleanup: close WorkerClient channels before stopping the gRPC server.
    # WorkerClient channels are created by RegisterWorker and stored in hass.data.
    # If not closed before the event loop shuts down, grpc raises
    # RuntimeError: Event loop is closed in its completion queue callbacks.
    from homeassistant.core_grpc.services.state_service import (  # noqa: PLC0415
        DATA_WORKER_CLIENTS,
    )

    clients = hass.data.pop(DATA_WORKER_CLIENTS, {})
    for client in clients.values():
        with contextlib.suppress(Exception):
            await client.close()

    # Cleanup: stop the gRPC server immediately (grace=0 drains the
    # completion queue synchronously, preventing stray callbacks after
    # the event loop closes).
    from homeassistant.core_grpc import DATA_GRPC_SERVER  # noqa: PLC0415

    server = hass.data.get(DATA_GRPC_SERVER)
    if server:
        await server.stop(grace=0)
    hass.data.pop(DATA_GRPC_SERVER, None)
    # Allow grpc's completion queue to fully drain before the loop closes.
    await asyncio.sleep(0.1)


@pytest.fixture
async def worker_registry(hass_with_grpc: HomeAssistant):
    """Create and start a WorkerRegistry with one ProcessWorker."""
    conf = [
        {
            "name": "Test Worker",
            "type": "process",
            "port": TEST_WORKER_PORT,
            "core_address": f"localhost:{TEST_GRPC_PORT}",
        }
    ]
    registry = WorkerRegistry(hass_with_grpc, conf)
    hass_with_grpc.data[DATA_WORKER_REGISTRY] = registry
    await registry.async_start()
    yield registry
    await registry.async_stop()
    hass_with_grpc.data.pop(DATA_WORKER_REGISTRY, None)


async def test_process_worker_starts_and_becomes_ready(
    hass_with_grpc: HomeAssistant,
    worker_registry: WorkerRegistry,
) -> None:
    """ProcessWorker subprocess starts and reaches RUNNING status."""
    worker = worker_registry.get_worker("Test Worker")
    assert worker is not None

    ready = await _wait_for_worker_ready(worker, timeout=30.0)
    assert ready, f"Worker did not become ready in time (status={worker.status})"
    assert worker.status == WORKER_STATUS_RUNNING
    assert worker.address == f"localhost:{TEST_WORKER_PORT}"


async def test_process_worker_grpc_reachable(
    hass_with_grpc: HomeAssistant,
    worker_registry: WorkerRegistry,
) -> None:
    """ProcessWorker gRPC port is reachable after startup."""
    worker = worker_registry.get_worker("Test Worker")
    assert worker is not None
    await _wait_for_worker_ready(worker, timeout=30.0)

    reachable = await worker.async_check_reachable()
    assert reachable, "Worker gRPC port is not reachable"


async def test_worker_registry_available_workers(
    hass_with_grpc: HomeAssistant,
    worker_registry: WorkerRegistry,
) -> None:
    """get_available_workers returns the running worker."""
    worker = worker_registry.get_worker("Test Worker")
    assert worker is not None
    await _wait_for_worker_ready(worker, timeout=30.0)

    available = worker_registry.get_available_workers()
    assert len(available) == 1
    assert available[0].name == "Test Worker"


async def test_get_entry_via_grpc(
    hass_with_grpc: HomeAssistant,
    worker_registry: WorkerRegistry,
) -> None:
    """Core gRPC GetEntry returns entry data for a known config entry.

    Calls the servicer directly against a real HomeAssistant instance
    (with the gRPC server and ProcessWorker running) rather than going through
    the gRPC network stack, to avoid grpc channel cleanup issues in tests.
    """
    from homeassistant.core_grpc.protos import core_pb2  # noqa: PLC0415
    from homeassistant.core_grpc.services.state_service import (  # noqa: PLC0415
        CoreServiceServicer,
    )

    # Create a fake config entry in hass
    entry = MagicMock()
    entry.domain = "test_integration"
    entry.title = "Test"
    entry.data = {"host": "192.168.1.1"}
    entry.options = {}
    hass_with_grpc.config_entries.async_get_entry = MagicMock(return_value=entry)
    # config_dir is already set by the hass fixture; custom_components/ won't
    # exist in the test config directory so GetEntry will use source="builtin".

    # Call the servicer directly — tests the real handler logic against
    # the running hass instance without the grpc network layer.
    servicer = CoreServiceServicer(hass_with_grpc)
    response = await servicer.GetEntry(
        core_pb2.GetEntryRequest(entry_id="test123"),  # type: ignore[attr-defined]
        MagicMock(),
    )
    assert response.found is True
    assert response.domain == "test_integration"


async def test_get_config_via_grpc(
    hass_with_grpc: HomeAssistant,
    worker_registry: WorkerRegistry,
) -> None:
    """Core gRPC GetConfig returns real HA config.

    Calls the servicer directly against a real HomeAssistant instance
    (with the gRPC server and ProcessWorker running) rather than going through
    the gRPC network stack, to avoid grpc channel cleanup issues in tests.
    """
    from homeassistant.core_grpc.protos import core_pb2  # noqa: PLC0415
    from homeassistant.core_grpc.services.state_service import (  # noqa: PLC0415
        CoreServiceServicer,
    )

    # Set known values on hass config
    hass_with_grpc.config.time_zone = "America/Toronto"
    hass_with_grpc.config.language = "fr"
    units = MagicMock()
    units.temperature_unit = "°C"
    units.length_unit = "km"
    units.mass_unit = "kg"
    units.pressure_unit = "hPa"
    units.volume_unit = "L"
    units.wind_speed_unit = "km/h"
    units.accumulated_precipitation_unit = "mm"
    hass_with_grpc.config.units = units
    hass_with_grpc.config.latitude = 45.5
    hass_with_grpc.config.longitude = -73.6
    hass_with_grpc.config.country = "CA"
    hass_with_grpc.config.currency = "CAD"

    # Call the servicer directly — tests the real handler logic against
    # the running hass instance without the grpc network layer.
    servicer = CoreServiceServicer(hass_with_grpc)
    response = await servicer.GetConfig(
        core_pb2.GetConfigRequest(),  # type: ignore[attr-defined]
        MagicMock(),
    )
    assert response.time_zone == "America/Toronto"
    assert response.language == "fr"
    assert response.temperature_unit == "°C"


async def test_worker_capacity_tracking(
    hass_with_grpc: HomeAssistant,
) -> None:
    """Worker capacity is tracked correctly via increment/decrement."""
    conf = [
        {
            "name": "Capped Worker",
            "type": "process",
            "port": TEST_WORKER_PORT,
            "core_address": f"localhost:{TEST_GRPC_PORT}",
            "max_integrations": 2,
        }
    ]
    registry = WorkerRegistry(hass_with_grpc, conf)
    worker = registry.get_worker("Capped Worker")
    assert worker is not None

    assert worker.has_capacity is True
    worker.increment_integrations()
    worker.increment_integrations()
    assert worker.has_capacity is False
    assert registry.get_available_workers() == []  # worker not running anyway

    worker.decrement_integrations()
    assert worker.has_capacity is True


async def test_config_flow_injects_worker_selection(
    hass_with_grpc: HomeAssistant,
    worker_registry: WorkerRegistry,
) -> None:
    """async_create_entry is intercepted when workers are available."""
    from homeassistant.config_entries import ConfigFlow  # noqa: PLC0415

    worker = worker_registry.get_worker("Test Worker")
    await _wait_for_worker_ready(worker, timeout=30.0)

    # Simulate a ConfigFlow calling async_create_entry.
    # source is a read-only property that reads from flow.context["source"],
    # so we only need to set context here.
    flow = ConfigFlow.__new__(ConfigFlow)
    flow.hass = hass_with_grpc
    flow.handler = "test"
    flow.context = {"source": "user"}
    flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "hs_worker_selection"}
    )
    flow._async_set_next_flow_if_valid = MagicMock()  # type: ignore[attr-defined]

    result = flow.async_create_entry(title="My Integration", data={"key": "value"})

    # Should have been intercepted → show form for worker selection.
    # Use .get() to satisfy type checkers (type and step_id are optional in TypedDict).
    assert result.get("type") == "form"
    assert result.get("step_id") == "hs_worker_selection"
    flow.async_show_form.assert_called_once()
