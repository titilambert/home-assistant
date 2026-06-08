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

# Populated by _apply_global_patches() with the singleton _MockEntityRegistry
# instance so that _setup_entity() can register entities without needing a
# hass reference.  None until the first HomeAssistantGrpcProxy is created.
_GLOBAL_ENTITY_REGISTRY: _MockEntityRegistry | None = None

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

    # ------------------------------------------------------------------
    # 3. device_registry / entity_registry / issue_registry → mock singletons
    # ------------------------------------------------------------------
    import homeassistant.helpers.device_registry as _dr
    import homeassistant.helpers.issue_registry as _ir

    _dr_instance = _MockDeviceRegistry()
    _dr.async_get = lambda hass: _dr_instance  # type: ignore[assignment]

    _er_instance = _MockEntityRegistry()
    _er.async_get = lambda hass: _er_instance  # type: ignore[assignment]

    # Keep a module-level reference so _setup_entity can reach it without a
    # hass reference (the lambda above captures _er_instance already, but we
    # need it accessible from _setup_entity which only has hass).
    import homeassistant.worker.proxy as _self_module

    _self_module._GLOBAL_ENTITY_REGISTRY = _er_instance  # type: ignore[attr-defined]

    _ir.async_create_issue = lambda hass, *args, **kwargs: None  # type: ignore[assignment]
    _ir.async_delete_issue = lambda hass, *args, **kwargs: None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 4. dispatcher → local in-memory implementation
    # ------------------------------------------------------------------
    import homeassistant.helpers.dispatcher as _dispatcher_module

    _dispatcher_module.async_dispatcher_connect = (  # type: ignore[assignment]
        lambda hass, signal, target: hass._dispatcher.connect(signal, target)
    )
    _dispatcher_module.dispatcher_send = (  # type: ignore[assignment]
        lambda hass, signal, *args: hass._dispatcher.send(signal, *args)
    )
    _dispatcher_module.async_dispatcher_send = (  # type: ignore[assignment]
        lambda hass, signal, *args: hass._dispatcher.send(signal, *args)
    )

    # ------------------------------------------------------------------
    # 5. event helpers → lightweight timer shims
    # ------------------------------------------------------------------
    from datetime import timedelta as _timedelta

    import homeassistant.helpers.event as _event_module

    def _async_track_time_interval(
        hass: Any, action: Any, interval: Any, **kwargs: Any
    ) -> Callable:
        """Schedule *action* to be called every *interval*."""
        seconds = (
            interval.total_seconds() if isinstance(interval, _timedelta) else interval
        )

        async def _loop() -> None:
            while not hass.is_stopping:
                await asyncio.sleep(seconds)
                if hass.is_stopping:
                    break
                try:
                    result = action(None)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("(proxy) async_track_time_interval error: %s", err)

        task = asyncio.create_task(_loop())
        return task.cancel

    _event_module.async_track_time_interval = _async_track_time_interval  # type: ignore[assignment]

    def _async_call_later(
        hass: Any, delay: Any, action: Any, **kwargs: Any
    ) -> Callable:
        """Schedule *action* to be called once after *delay* seconds."""
        loop = asyncio.get_event_loop()

        def _callback() -> None:
            result = action(None)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)

        handle = loop.call_later(delay, _callback)
        return handle.cancel

    _event_module.async_call_later = _async_call_later  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 6. helpers.storage.Store → in-memory mock (no disk I/O)
    # ------------------------------------------------------------------
    import homeassistant.helpers.storage as _storage_module

    _storage_module.Store = _MockStore  # type: ignore[assignment]

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


class _MockDeviceEntry:
    """Minimal device entry stub returned by _MockDeviceRegistry."""

    def __init__(self, id: str, area_id: str | None = None) -> None:
        self.id = id
        self.area_id = area_id
        self.name: str | None = None
        self.model: str | None = None
        self.manufacturer: str | None = None


class _MockDeviceRegistry:
    """No-op device registry shim for the remote worker."""

    def async_get_or_create(self, **kwargs: Any) -> _MockDeviceEntry:
        """Return a minimal device entry stub with a stable fake device_id."""
        import uuid

        identifiers = kwargs.get("identifiers", set())
        device_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS,
                str(sorted(str(i) for i in identifiers)),
            )
        )
        return _MockDeviceEntry(id=device_id, area_id=None)

    def async_get(self, device_id: str) -> None:
        return None

    def async_update_device(self, device_id: str, **kwargs: Any) -> None:
        pass


class _MockEntityRegistry:
    """Lightweight entity registry shim for the remote worker.

    Stores a mapping of (domain, platform, unique_id) → entity_id so that
    async_get_entity_id() returns a stable result after _setup_entity() has
    registered an entity.  Everything else remains a no-op.
    """

    def __init__(self) -> None:
        # (domain, platform, unique_id) -> entity_id
        self._uid_map: dict[tuple[str, str, str], str] = {}
        # entity_id -> unique_id  (reverse lookup, used by _push_state)
        self._entity_uid: dict[str, str] = {}

    def async_register(
        self,
        domain: str,
        platform: str,
        unique_id: str,
        entity_id: str,
    ) -> None:
        """Record the mapping between a unique_id and its entity_id."""
        key = (domain, platform, unique_id)
        self._uid_map[key] = entity_id
        self._entity_uid[entity_id] = unique_id

    def async_get_entity_id(
        self, domain: str, platform: str, unique_id: str
    ) -> str | None:
        return self._uid_map.get((domain, platform, unique_id))

    def get_unique_id(self, entity_id: str) -> str | None:
        """Return the unique_id for a given entity_id, or None."""
        return self._entity_uid.get(entity_id)

    def async_get(self, entity_id: str) -> None:
        return None

    def async_entries_for_config_entry(self, config_entry_id: str) -> list:
        return []

    def async_entries_for_device(
        self, device_id: str, include_disabled_entries: bool = False
    ) -> list:
        return []

    def async_update_entity(self, entity_id: str, **kwargs: Any) -> None:
        pass


class _MockIssueRegistry:
    """No-op issue registry shim."""


class _LocalDispatcher:
    """Local in-memory dispatcher replacing homeassistant.helpers.dispatcher."""

    def __init__(self) -> None:
        self._listeners: dict[str, list] = {}

    def connect(self, signal: str, target: Callable) -> Callable:
        """Subscribe *target* to *signal*; return an unsubscribe callable."""
        self._listeners.setdefault(signal, []).append(target)

        def _remove() -> None:
            try:
                self._listeners.get(signal, []).remove(target)
            except ValueError:
                pass

        return _remove

    def send(self, signal: str, *args: Any, **kwargs: Any) -> None:
        """Dispatch *signal* synchronously to all registered listeners."""
        for listener in list(self._listeners.get(signal, [])):
            try:
                listener(*args, **kwargs)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("(proxy) Dispatcher error for signal %s: %s", signal, err)

    async def async_send(self, signal: str, *args: Any, **kwargs: Any) -> None:
        """Async variant — delegates to the synchronous send."""
        self.send(signal, *args, **kwargs)


class _MockStore:
    """In-memory replacement for homeassistant.helpers.storage.Store."""

    def __init__(self, hass: Any, version: Any, key: str, **kwargs: Any) -> None:
        self._key = key
        self._data: Any = None

    async def async_load(self) -> Any:
        return self._data

    async def async_save(self, data: Any) -> None:
        self._data = data

    def async_delay_save(self, data_func: Callable, delay: float = 0) -> None:
        async def _save() -> None:
            await asyncio.sleep(delay)
            self._data = data_func()

        asyncio.create_task(_save())


class _MockUnits:
    """Minimal UnitSystem shim."""

    temperature_unit = "°C"
    length_unit = "km"
    mass_unit = "kg"
    pressure_unit = "hPa"
    volume_unit = "L"
    wind_speed_unit = "km/h"
    accumulated_precipitation_unit = "mm"
    area_unit = "m²"


class _MockConfig:
    """Minimal hass.config shim."""

    def __init__(self) -> None:
        self.time_zone = "UTC"
        self.units = _MockUnits()
        self.language = "en"
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
        _entry_id: str = "",
        **kwargs: Any,
    ) -> None:
        """Schedule async_set as a fire-and-forget coroutine."""
        # Use _entry_id if provided (from _push_state), otherwise fall back to
        # the proxy-level entry_id (single-integration mode).
        entry_id = _entry_id or self._entry_id
        asyncio.create_task(
            self._async_set(entity_id, new_state, attributes or {}, entry_id)
        )

    async def _async_set(
        self, entity_id: str, new_state: str, attributes: dict, entry_id: str = ""
    ) -> None:
        from homeassistant.core_grpc.protos import core_pb2

        try:
            await self._stub.SetState(
                core_pb2.SetStateRequest(
                    entity_id=entity_id,
                    state=new_state,
                    attributes={k: str(v) for k, v in attributes.items()},
                    entry_id=entry_id,
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
        from homeassistant.core_grpc.protos import core_pb2

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
                hass: Any,
                platform_name_local: str,
                integration_name: str,
                config_entry_id: str,
            ) -> Any:
                def async_add_entities(
                    entities: list, update_before_add: bool = False
                ) -> None:
                    for entity in entities:
                        _setup_entity(
                            hass,
                            entity,
                            platform_name_local,
                            integration_name,
                            config_entry_id,
                        )

                return async_add_entities

            def _setup_entity(
                hass: Any,
                entity: Any,
                platform_name_local: str,
                integration_name: str,
                config_entry_id: str,
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

                # Build a stable entity_id from the entity *name*, mirroring
                # what real HA does (entity_id is derived from the friendly
                # name, not the unique_id).
                #
                # Priority:
                #   1. entity.name  (property, may return a translation key object)
                #   2. entity._name (raw string stored by PiHoleEntity.__init__)
                #   3. Fallback: sanitised unique_id  (old behaviour, last resort)
                #
                # Example: name="Pi-Hole" → entity_id="switch.pi_hole"
                if (
                    not getattr(entity, "entity_id", None)
                    or entity.entity_id == "unknown.unknown"
                ):
                    import re

                    # Resolve the best available name string.
                    raw_name: str | None = None
                    try:
                        n = entity.name
                        if isinstance(n, str):
                            raw_name = n
                    except Exception:  # noqa: BLE001
                        pass
                    if not raw_name:
                        raw_name = getattr(entity, "_name", None)
                    if not raw_name:
                        # Last resort: fall back to the unique_id
                        raw_name = getattr(entity, "unique_id", None) or str(id(entity))

                    # Also append a suffix derived from the entity description key
                    # or unique_id so that multiple entities of the same platform
                    # (e.g. all Pi-hole sensors) get distinct entity_ids.
                    #
                    # Priority for suffix:
                    #   1. entity_description.key  (e.g. "queries.blocked")
                    #   2. unique_id suffix after the first "/" (e.g. "queries.blocked")
                    #   3. No suffix (single entity per platform)
                    suffix: str = ""
                    desc = getattr(entity, "entity_description", None)
                    if desc is not None:
                        desc_key = getattr(desc, "key", None)
                        if desc_key:
                            suffix = re.sub(
                                r"[^a-z0-9]+", "_", str(desc_key).lower()
                            ).strip("_")
                    if not suffix:
                        uid = getattr(entity, "unique_id", None)
                        if uid and "/" in uid:
                            suffix = re.sub(
                                r"[^a-z0-9]+", "_", uid.split("/", 1)[1].lower()
                            ).strip("_")

                    safe_name = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_")
                    if suffix and suffix != safe_name:
                        entity.entity_id = f"{platform_name_local}.{safe_name}_{suffix}"
                    else:
                        entity.entity_id = f"{platform_name_local}.{safe_name}"

                # Register the entity in the mock entity registry so that
                # async_get_entity_id() returns a stable result and
                # _push_state() can retrieve the unique_id for transmission.
                unique_id_val = getattr(entity, "unique_id", None)
                if unique_id_val:
                    import homeassistant.worker.proxy as _rh

                    er = getattr(_rh, "_GLOBAL_ENTITY_REGISTRY", None)
                    if er is not None:
                        er.async_register(
                            platform_name_local,
                            integration_name,
                            unique_id_val,
                            entity.entity_id,
                        )

                # Initial state push
                _push_state(hass, entity, config_entry_id)

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

                    def _make_listener(e: Any, eid: str) -> Any:
                        def _on_update(*_args: Any) -> None:
                            _push_state(hass, e, eid)

                        return _on_update

                    remove_listener = coordinator.async_add_listener(
                        _make_listener(entity, config_entry_id)
                    )
                    # Store the unsubscribe function on the entity for cleanup
                    entity._remote_remove_listener = remove_listener  # noqa: SLF001

            def _push_state(hass: Any, entity: Any, config_entry_id: str = "") -> None:
                """Read entity state and push it to Core via gRPC (hass.states.async_set).

                In addition to the integration-defined extra_state_attributes we
                inject a set of well-known ``_ha_*`` meta-attributes so that the
                Core gRPC servicer can:

                * set a human-readable ``friendly_name`` on the state,
                * record the ``unique_id`` in the entity registry (problem 2),
                * link the entity to its device via ``device_info`` (problem 3).

                These attributes are prefixed with ``_ha_`` to avoid collisions
                with real integration attributes.  The Core servicer strips them
                before storing the state in hass.states.
                """
                import json

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

                    # ----------------------------------------------------------
                    # Problem 2 — unique_id
                    # ----------------------------------------------------------
                    unique_id_val = getattr(entity, "unique_id", None)
                    if unique_id_val:
                        attrs["_ha_unique_id"] = str(unique_id_val)

                    # ----------------------------------------------------------
                    # Problem 1 (complement) — friendly_name
                    # With has_entity_name=True + translation_key, entity.name
                    # returns the device name ("Pi-Hole") for all entities.
                    # We resolve the individual entity name from strings.json
                    # using the translation_key from the entity_description.
                    # ----------------------------------------------------------
                    friendly_name: str | None = None

                    # Try to resolve from translation_key in strings.json
                    try:
                        desc = getattr(entity, "entity_description", None)
                        translation_key = (
                            getattr(desc, "translation_key", None) if desc else None
                        )
                        integration_domain = getattr(entity, "platform_data", None)
                        platform_name_str = (
                            integration_domain.platform_name
                            if integration_domain
                            else None
                        )

                        if translation_key and platform_name_str:
                            import json as _json
                            import pathlib as _pl

                            # Find strings.json for this integration
                            components_path = (
                                _pl.Path(__file__).parent.parent
                                / "components"
                                / platform_name_str
                                / "strings.json"
                            )
                            if components_path.exists():
                                with open(components_path) as _f:
                                    _strings = _json.load(_f)
                                # Walk entity.<platform_domain>.<translation_key>.name
                                # e.g. entity.sensor.ads_blocked.name
                                entity_domain = (
                                    entity.entity_id.split(".")[0]
                                    if entity.entity_id
                                    else None
                                )
                                if entity_domain:
                                    _name_val = (
                                        _strings.get("entity", {})
                                        .get(entity_domain, {})
                                        .get(translation_key, {})
                                        .get("name")
                                    )
                                    if isinstance(_name_val, str):
                                        friendly_name = _name_val
                    except Exception:  # noqa: BLE001
                        pass

                    # Fallback: entity._name (device name) only if no translation found
                    if not friendly_name:
                        try:
                            n = entity.name
                            if isinstance(n, str):
                                friendly_name = n
                        except Exception:  # noqa: BLE001
                            pass
                    if not friendly_name:
                        friendly_name = getattr(entity, "_name", None)

                    if friendly_name:
                        attrs["_ha_friendly_name"] = friendly_name

                    # ----------------------------------------------------------
                    # Problem 3 — device_info
                    # Serialise DeviceInfo (a TypedDict / NamedTuple) to JSON so
                    # the Core servicer can register / update the device entry.
                    # ----------------------------------------------------------
                    try:
                        device_info = entity.device_info
                        if device_info is not None:
                            # DeviceInfo is a dict subclass in modern HA.
                            di_serialisable: dict = {}
                            for k, v in dict(device_info).items():
                                try:
                                    # identifiers is a set of tuples — make it
                                    # JSON-serialisable.
                                    if k == "identifiers":
                                        di_serialisable[k] = [list(item) for item in v]
                                    elif hasattr(v, "__str__"):
                                        di_serialisable[k] = str(v)
                                    else:
                                        di_serialisable[k] = v
                                except Exception:  # noqa: BLE001
                                    pass
                            attrs["_ha_device_info"] = json.dumps(di_serialisable)
                    except Exception:  # noqa: BLE001
                        pass

                    # Pass config_entry_id so Core can map entity_id → entry_id
                    # and route service calls to the correct worker.
                    hass.states.async_set(
                        entity_id,
                        str(state),
                        attrs,
                        _entry_id=config_entry_id,
                    )
                    _LOGGER.debug("(proxy) pushed state %s = %s", entity_id, state)
                except Exception as err:
                    _LOGGER.error(
                        "(proxy) _push_state failed for %s: %s",
                        entity,
                        err,
                        exc_info=True,
                    )

            try:
                await platform_module.async_setup_entry(
                    self._hass,
                    entry,
                    # platform_name = "switch"/"sensor"/… , domain = "pi_hole"
                    _make_add_entities(
                        self._hass, platform_name, domain, entry.entry_id
                    ),
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

    Implements the subset of the hass API needed by Pi-hole, UniFi, Shelly,
    and similar integrations.  Incompatible HA helpers (registries, dispatcher,
    storage, timers, aiohttp client factory) are monkey-patched at construction
    time so that integration code runs unchanged without a live HA instance.
    """

    def __init__(self, core_address: str, entry_id: str) -> None:
        """Initialize the proxy and connect to the Core gRPC server."""
        # Apply global module-level patches before any integration code can run.
        _apply_global_patches()

        from homeassistant.core_grpc.protos import core_pb2_grpc

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
        )
        # Local dispatcher — used by the patched homeassistant.helpers.dispatcher shims
        self._dispatcher = _LocalDispatcher()
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
