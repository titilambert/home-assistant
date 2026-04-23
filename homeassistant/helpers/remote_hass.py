"""HomeAssistantGrpcProxy: transparent gRPC proxy replacing the hass object in remote integrations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import logging
import tempfile
from typing import Any

import aiohttp
import grpc
import grpc.aio

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Monkey-patches applied once when the first HomeAssistantGrpcProxy is created
# ---------------------------------------------------------------------------

_GLOBAL_PATCHES_APPLIED = False

# The proxy replacement for async_get_clientsession, kept at module level so
# that patch_integration_namespace() can re-use it after integration import.
_DATA_PROXY_SESSION = "_remote_worker_aiohttp_session"


def _proxy_async_get_clientsession(
    hass: Any,
    verify_ssl: bool = True,
    **_kwargs: Any,
) -> aiohttp.ClientSession:
    """Lightweight aiohttp session factory for the remote worker.

    Replaces homeassistant.helpers.aiohttp_client.async_get_clientsession so
    that integration code gets a real HTTP session without going through the
    full HA aiohttp machinery (SSRF middleware, zeroconf resolver, …).
    """
    # One shared session is enough — HoleV6 passes ssl=verify_tls on every
    # individual request, so the connector itself does not need to enforce it.
    if "default" not in hass.data.setdefault(_DATA_PROXY_SESSION, {}):
        connector = aiohttp.TCPConnector()
        hass.data[_DATA_PROXY_SESSION]["default"] = aiohttp.ClientSession(
            connector=connector
        )
        _LOGGER.debug("(proxy) Created shared aiohttp.ClientSession")
    sessions = hass.data[_DATA_PROXY_SESSION]
    key = "default"
    return sessions["default"]


def _apply_global_patches() -> None:
    """Monkey-patch HA helper *modules* once per process.

    Patches the canonical module-level names so that any future
    ``from homeassistant.helpers.X import Y`` gets the proxy version.
    Already-bound references in integration modules are NOT fixed here —
    call patch_integration_namespace() after importing the integration.
    """
    global _GLOBAL_PATCHES_APPLIED  # noqa: PLW0603
    if _GLOBAL_PATCHES_APPLIED:
        return
    _GLOBAL_PATCHES_APPLIED = True

    # ------------------------------------------------------------------
    # 1. entity_registry.async_migrate_entries → no-op
    #    Pi-hole calls this to rename unique IDs.  The real entity registry
    #    lives in Core; the worker must not try to instantiate it.
    # ------------------------------------------------------------------
    import homeassistant.helpers.entity_registry as _er

    async def _noop_async_migrate_entries(
        hass: Any,
        entry_id: str,
        entry_callback: Callable,
    ) -> None:
        _LOGGER.debug(
            "(proxy) entity_registry.async_migrate_entries for entry %s (no-op)",
            entry_id,
        )

    _er.async_migrate_entries = _noop_async_migrate_entries  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 2. aiohttp_client.async_get_clientsession → proxy session factory
    # ------------------------------------------------------------------
    import homeassistant.helpers.aiohttp_client as _aiohttp_client

    _aiohttp_client.async_get_clientsession = _proxy_async_get_clientsession  # type: ignore[assignment]

    _LOGGER.debug("(proxy) Global patches applied.")


def patch_integration_namespace(module_name: str) -> None:
    """Fix already-bound names in an integration module after it is imported.

    When an integration does::

        from homeassistant.helpers.aiohttp_client import async_get_clientsession

    …at module level, it stores a direct reference to the original function.
    Patching the helper module afterwards has no effect on that reference.
    This function re-binds the patched names in the integration's own namespace.

    Call this *after* importing the integration module (so it is in
    sys.modules) and *before* calling async_setup_entry().

    Args:
        module_name: Fully-qualified module name, e.g.
                     ``"homeassistant.components.pi_hole"``.
    """
    import sys

    mod = sys.modules.get(module_name)
    if mod is None:
        _LOGGER.debug(
            "(proxy) patch_integration_namespace: %s not in sys.modules, skipping",
            module_name,
        )
        return

    if hasattr(mod, "async_get_clientsession"):
        mod.async_get_clientsession = _proxy_async_get_clientsession  # type: ignore[assignment]
        _LOGGER.debug("(proxy) Patched async_get_clientsession in %s", module_name)

    _LOGGER.debug("(proxy) Integration namespace patched: %s", module_name)


# Backwards-compatible alias so existing call sites don't break.
def _apply_patches() -> None:
    """Apply global patches (backwards-compatible alias)."""
    _apply_global_patches()


# ---------------------------------------------------------------------------
# Shims
# ---------------------------------------------------------------------------


class _MockConfig:
    """Minimal hass.config shim."""

    def __init__(self) -> None:
        self.time_zone = "UTC"
        self.units = None
        # Some helpers (storage, entity_registry) need config_dir.  We point
        # to a throw-away temp directory — nothing should actually be written
        # there in normal operation because the relevant helpers are patched
        # out, but having the attribute prevents AttributeError crashes.
        self.config_dir = tempfile.mkdtemp(prefix="ha_remote_worker_")


class _MockBus:
    """Minimal hass.bus shim — events are no-ops in the remote worker."""

    def async_fire(self, event_type: str, event_data: dict | None = None) -> None:
        _LOGGER.debug("(proxy) bus.async_fire: %s %s", event_type, event_data)

    def async_listen(self, event_type: str, listener: Callable) -> Callable:
        _LOGGER.debug("(proxy) bus.async_listen: %s", event_type)
        return lambda: None

    def async_listen_once(self, event_type: str, listener: Callable) -> Callable:
        _LOGGER.debug("(proxy) bus.async_listen_once: %s", event_type)
        return lambda: None


class StatesProxy:
    """Proxy for hass.states — forwards async_set to Core via gRPC."""

    def __init__(self, stub: Any, entry_id: str = "") -> None:
        self._stub = stub
        self._entry_id = entry_id

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
                    entry_id=self._entry_id,
                )
            )
            _LOGGER.debug("(proxy) SetState OK: %s = %s", entity_id, new_state)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("(proxy) SetState failed for %s: %s", entity_id, err)

    def get(self, entity_id: str) -> None:
        """Return None — no local state cache in the remote worker."""
        return


class ServicesProxy:
    """Proxy for hass.services — forwards registrations to Core via gRPC."""

    def __init__(self, stub: Any, entry_id: str) -> None:
        self._stub = stub
        self._entry_id = entry_id
        self._handlers: dict[tuple[str, str], Callable] = {}

    def async_register(
        self,
        domain: str,
        service: str,
        service_func: Callable,
        schema: Any = None,
    ) -> None:
        """Register a service handler locally and notify Core."""
        _LOGGER.info(
            "(proxy) ServicesProxy.async_register called: %s.%s", domain, service
        )
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
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "(proxy) RegisterService failed %s.%s: %s", domain, service, err
            )

    async def async_call(
        self, domain: str, service: str, service_data: dict | None = None
    ) -> None:
        """Call a locally-registered service handler."""
        handler = self._handlers.get((domain, service))
        if handler is not None:
            await handler(service_data or {})
        else:
            _LOGGER.warning(
                "(proxy) No local handler for service %s.%s", domain, service
            )


class _MockConfigEntries:
    """Minimal hass.config_entries shim."""

    def __init__(self, entry_id: str, hass: Any) -> None:
        self._entry_id = entry_id
        self._hass = hass

    def async_update_entry(self, entry: Any, **kwargs: Any) -> None:
        _LOGGER.debug("(proxy) config_entries.async_update_entry (no-op)")

    async def async_forward_entry_setups(self, entry: Any, platforms: list) -> None:
        """Set up each platform and wire coordinator updates to gRPC SetState calls."""
        import sys

        domain = entry.domain
        _LOGGER.debug(
            "(proxy) config_entries.async_forward_entry_setups for %s", platforms
        )

        for platform in platforms:
            platform_name = (
                str(platform.value) if hasattr(platform, "value") else str(platform)
            )
            module_name = f"homeassistant.components.{domain}.{platform_name}"
            try:
                # Import the platform module
                if module_name not in sys.modules:
                    import importlib

                    importlib.import_module(module_name)
                platform_module = sys.modules[module_name]
            except ImportError:
                _LOGGER.warning(
                    "(proxy) Could not import platform module %s", module_name
                )
                continue

            if not hasattr(platform_module, "async_setup_entry"):
                _LOGGER.debug(
                    "(proxy) No async_setup_entry in %s, skipping", module_name
                )
                continue

            # Build a custom async_add_entities that wires each entity to gRPC
            # platform_name = "switch", "sensor", etc.  (the HA domain of the entity)
            # integration_name = "pi_hole"              (the integration domain)
            def _make_add_entities(
                hass: Any, platform_name_local: str, integration_name: str
            ) -> Any:
                def async_add_entities(
                    entities: list, update_before_add: bool = False
                ) -> None:
                    for entity in entities:
                        _setup_entity(
                            hass, entity, platform_name_local, integration_name
                        )

                return async_add_entities

            def _setup_entity(
                hass: Any, entity: Any, platform_name_local: str, integration_name: str
            ) -> None:
                """Attach hass to entity and subscribe to coordinator updates."""
                from homeassistant.helpers.entity import EntityPlatformState
                from homeassistant.helpers.entity_platform import PlatformData

                entity.hass = hass

                # Attach minimal PlatformData so translation_key lookups work.
                # domain = platform ("switch", "sensor", …)
                # platform_name = integration ("pi_hole")
                entity.platform_data = PlatformData(
                    hass,
                    domain=platform_name_local,
                    platform_name=integration_name,
                )

                # Mark the entity as fully added to a platform so that
                # _async_write_ha_state / state property don't bail out early
                entity._platform_state = EntityPlatformState.ADDED  # noqa: SLF001

                # Mark entity as state-writable (bypasses internal HA checks)
                entity._verified_state_writable = True  # noqa: SLF001

                # Build a stable entity_id: {platform}.{sanitised_unique_id}
                # e.g. switch.pi_hole_abc123_switch
                if (
                    not getattr(entity, "entity_id", None)
                    or entity.entity_id == "unknown.unknown"
                ):
                    unique_id = getattr(entity, "unique_id", None) or str(id(entity))
                    # Sanitise: lowercase, replace every non-alphanumeric char with _
                    import re

                    safe_uid = re.sub(r"[^a-z0-9]+", "_", unique_id.lower()).strip("_")
                    entity.entity_id = f"{platform_name_local}.{safe_uid}"

                # Initial state push
                _push_state(hass, entity)

                # Register turn_on/turn_off as service handlers if the entity supports them.
                # We call hass.services.async_register so that ServicesProxy notifies
                # the Core via gRPC RegisterService — which is what triggers the Core
                # to install a routing handler for switch.turn_on / switch.turn_off.
                if hasattr(entity, "async_turn_on"):
                    _e_on = entity

                    def _turn_on_handler(call: Any, e: Any = _e_on) -> Any:
                        return e.async_turn_on()

                    hass.services.async_register(
                        platform_name_local, "turn_on", _turn_on_handler
                    )

                if hasattr(entity, "async_turn_off"):
                    _e_off = entity

                    def _turn_off_handler(call: Any, e: Any = _e_off) -> Any:
                        return e.async_turn_off()

                    hass.services.async_register(
                        platform_name_local, "turn_off", _turn_off_handler
                    )

                # Subscribe to coordinator updates
                coordinator = getattr(entity, "coordinator", None)
                if coordinator is not None:

                    def _make_listener(e: Any) -> Any:
                        def _on_update(*_args: Any) -> None:
                            _push_state(hass, e)

                        return _on_update

                    remove_listener = coordinator.async_add_listener(
                        _make_listener(entity)
                    )
                    # Store the unsubscribe function on the entity for cleanup
                    entity._remote_remove_listener = remove_listener  # noqa: SLF001

            def _push_state(hass: Any, entity: Any) -> None:
                """Read entity state and push it to Core via gRPC (hass.states.async_set)."""
                try:
                    state = entity.state
                    if state is None:
                        return
                    attrs: dict = {}
                    try:
                        attrs = dict(entity.extra_state_attributes or {})
                    except Exception:  # noqa: BLE001
                        pass
                    entity_id = getattr(entity, "entity_id", None)
                    if not entity_id:
                        return
                    hass.states.async_set(entity_id, str(state), attrs)
                    _LOGGER.debug("(proxy) pushed state %s = %s", entity_id, state)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("(proxy) _push_state failed for %s: %s", entity, err)

            try:
                await platform_module.async_setup_entry(
                    self._hass,
                    entry,
                    # platform_name = "switch"/"sensor"/… , domain = "pi_hole"
                    _make_add_entities(self._hass, platform_name, domain),
                )
                _LOGGER.debug("(proxy) Platform %s set up successfully", platform_name)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "(proxy) Failed to set up platform %s: %s", platform_name, err
                )

    async def async_unload_platforms(self, entry: Any, platforms: list) -> bool:
        return True


# ---------------------------------------------------------------------------
# Main proxy class
# ---------------------------------------------------------------------------


class HomeAssistantGrpcProxy:
    """Transparent proxy replacing the hass object in remote integrations.

    Only implements the subset of the hass API that the Pi-hole integration
    actually uses.  Incompatible HA helpers (entity registry, aiohttp client
    factory) are monkey-patched at construction time so that integration code
    runs unchanged without needing a live Home Assistant instance.
    """

    def __init__(self, core_address: str, entry_id: str) -> None:
        """Initialize the proxy and connect to the Core gRPC server."""
        # Apply global module-level patches before any integration code can run.
        _apply_global_patches()

        from homeassistant.grpc.protos import core_pb2_grpc

        self._channel = grpc.aio.insecure_channel(core_address)
        self._stub = core_pb2_grpc.CoreServiceStub(self._channel)

        self.states = StatesProxy(self._stub, entry_id)
        self.services = ServicesProxy(self._stub, entry_id)
        self.bus = _MockBus()
        self.config = _MockConfig()
        self.data: dict[str, Any] = {}
        self.loop = asyncio.get_event_loop()
        self.is_stopping = False
        self.loop_thread_id = (
            self.loop._thread_id if hasattr(self.loop, "_thread_id") else 0
        )  # noqa: SLF001
        # config_entries needs a reference to hass (self) so it can pass it to platforms
        self.config_entries = _MockConfigEntries(entry_id, self)

    async def async_create_task(
        self, target: Coroutine, name: str | None = None
    ) -> asyncio.Task:
        """Create an asyncio task."""
        return asyncio.create_task(target, name=name)

    async def async_add_executor_job(self, func: Callable, *args: Any) -> Any:
        """Run a blocking function in a thread-pool executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    def async_run_hass_job(
        self, hassjob: Any, *args: Any, background: bool = False
    ) -> Any:
        """Run a HassJob from within the event loop."""
        from homeassistant.core import HassJobType

        if hassjob.job_type is HassJobType.Coroutinefunction:
            return asyncio.ensure_future(hassjob.target(*args))
        if hassjob.job_type is HassJobType.Callback:
            return hassjob.target(*args)
        return asyncio.ensure_future(
            asyncio.get_event_loop().run_in_executor(None, hassjob.target, *args)
        )

    async def close(self) -> None:
        """Close the gRPC channel and any open aiohttp sessions."""
        # Close sessions created by the patched async_get_clientsession.
        _DATA_PROXY_SESSION = "_remote_worker_aiohttp_session"
        for session in self.data.get(_DATA_PROXY_SESSION, {}).values():
            if not session.closed:
                await session.close()
        await self._channel.close()
