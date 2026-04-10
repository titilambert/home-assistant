"""HomeAssistantGrpcProxy: transparent gRPC proxy replacing the hass object in remote integrations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import logging
from typing import Any

import grpc
import grpc.aio

_LOGGER = logging.getLogger(__name__)


class _MockLoop:
    """Minimal event loop shim."""

    def run_until_complete(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)


class _MockConfig:
    """Minimal hass.config shim."""

    def __init__(self) -> None:
        self.time_zone = "UTC"
        self.units = None


class _MockBus:
    """Minimal hass.bus shim (fire events are no-ops for POC)."""

    def async_fire(self, event_type: str, event_data: dict | None = None) -> None:
        _LOGGER.debug("(proxy) bus.async_fire: %s %s", event_type, event_data)

    def async_listen(self, event_type: str, listener) -> Callable:
        _LOGGER.debug("(proxy) bus.async_listen: %s", event_type)
        return lambda: None


class StatesProxy:
    """Proxy for hass.states — forwards async_set to Core via gRPC."""

    def __init__(self, stub) -> None:
        self._stub = stub

    def async_set(
        self,
        entity_id: str,
        new_state: str,
        attributes: dict | None = None,
        **kwargs: Any,
    ) -> None:
        """Schedule async_set as a fire-and-forget coroutine."""
        asyncio.create_task(self._async_set(entity_id, new_state, attributes or {}))

    async def _async_set(
        self, entity_id: str, new_state: str, attributes: dict
    ) -> None:
        from homeassistant.grpc.protos import core_pb2

        try:
            await self._stub.SetState(
                core_pb2.SetStateRequest(
                    entity_id=entity_id,
                    state=new_state,
                    attributes={k: str(v) for k, v in attributes.items()},
                )
            )
            _LOGGER.debug("(proxy) SetState OK: %s = %s", entity_id, new_state)
        except Exception as err:
            _LOGGER.error("(proxy) SetState failed for %s: %s", entity_id, err)

    def get(self, entity_id: str):
        """Return None for POC (no local cache)."""
        return None


class ServicesProxy:
    """Proxy for hass.services — forwards registrations to Core via gRPC."""

    def __init__(self, stub, entry_id: str) -> None:
        self._stub = stub
        self._entry_id = entry_id
        self._handlers: dict[tuple[str, str], Callable] = {}

    def async_register(
        self,
        domain: str,
        service: str,
        service_func: Callable,
        schema=None,
    ) -> None:
        """Register a service handler locally and notify Core."""
        self._handlers[(domain, service)] = service_func
        asyncio.create_task(self._notify_core(domain, service))

    async def _notify_core(self, domain: str, service: str) -> None:
        from homeassistant.grpc.protos import core_pb2

        try:
            await self._stub.RegisterService(
                core_pb2.RegisterServiceRequest(
                    domain=domain,
                    service=service,
                    entry_id=self._entry_id,
                )
            )
            _LOGGER.debug("(proxy) RegisterService OK: %s.%s", domain, service)
        except Exception as err:
            _LOGGER.error(
                "(proxy) RegisterService failed %s.%s: %s", domain, service, err
            )

    async def async_call(
        self, domain: str, service: str, service_data: dict | None = None
    ) -> None:
        """Call a service handler locally if registered."""
        handler = self._handlers.get((domain, service))
        if handler is not None:
            await handler(service_data or {})


class _MockConfigEntries:
    """Minimal hass.config_entries shim."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    def async_update_entry(self, entry, **kwargs) -> None:
        _LOGGER.debug("(proxy) config_entries.async_update_entry (no-op)")

    async def async_forward_entry_setups(self, entry, platforms) -> None:
        _LOGGER.debug(
            "(proxy) config_entries.async_forward_entry_setups for %s (no-op for POC)",
            platforms,
        )

    async def async_unload_platforms(self, entry, platforms) -> bool:
        return True


class HomeAssistantGrpcProxy:
    """Transparent proxy replacing the hass object in remote integrations.

    Only implements the subset of the hass API that the Pi-hole integration uses.
    """

    def __init__(self, core_address: str, entry_id: str) -> None:
        """Initialize the proxy and connect to the Core gRPC server."""
        from homeassistant.grpc.protos import core_pb2_grpc

        self._channel = grpc.aio.insecure_channel(core_address)
        self._stub = core_pb2_grpc.CoreServiceStub(self._channel)

        self.states = StatesProxy(self._stub)
        self.services = ServicesProxy(self._stub, entry_id)
        self.bus = _MockBus()
        self.config = _MockConfig()
        self.config_entries = _MockConfigEntries(entry_id)
        self.data: dict[str, Any] = {}
        self.loop = asyncio.get_event_loop()

    async def async_create_task(
        self, target: Coroutine, name: str | None = None
    ) -> asyncio.Task:
        """Create an asyncio task."""
        return asyncio.create_task(target, name=name)

    async def async_add_executor_job(self, func: Callable, *args: Any) -> Any:
        """Run a blocking function in an executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    async def close(self) -> None:
        """Close the gRPC channel."""
        await self._channel.close()
