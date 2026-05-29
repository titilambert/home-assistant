"""State service gRPC implementation for the Core gRPC server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from homeassistant.core import callback
from homeassistant.grpc.protos import core_pb2, core_pb2_grpc

_LOGGER = logging.getLogger(__name__)

DATA_WORKER_CLIENTS = "grpc_worker_clients"  # dict[entry_id, WorkerClient]

# Maps entity_id -> entry_id, populated on every SetState call.
# Used by the remote service handler to determine which worker owns an entity.
DATA_ENTITY_ENTRY = "grpc_entity_entry"  # dict[entity_id, entry_id]

# Maps (domain, service) -> set[entry_id] that have registered this service.
# Allows the routing handler to know which workers can handle a given service,
# and lets us avoid re-registering the same HA-level handler twice.
DATA_REMOTE_SERVICES = "grpc_remote_services"  # dict[tuple[str,str], set[entry_id]]


class CoreServiceServicer(core_pb2_grpc.CoreServiceServicer):
    """gRPC servicer that handles requests from remote integrations."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the servicer."""
        self.hass = hass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _entity_entry_map(self) -> dict[str, str]:
        """Return (and lazily create) the entity_id -> entry_id mapping."""
        return self.hass.data.setdefault(DATA_ENTITY_ENTRY, {})

    def _remote_services_map(self) -> dict[tuple[str, str], set[str]]:
        """Return (and lazily create) the (domain, service) -> {entry_id} mapping."""
        return self.hass.data.setdefault(DATA_REMOTE_SERVICES, {})

    def _make_remote_handler(self, domain: str, service: str):
        """Build an async service handler that routes calls to the right worker.

        The handler inspects the ``entity_id`` field in the service call data
        to look up which worker (entry_id) owns that entity, then forwards the
        call via the corresponding WorkerClient.

        If no entity_id is present (rare for switch/light/… services) the
        handler falls through and logs a warning.
        """
        from homeassistant.core import ServiceCall

        async def _remote_handler(call: ServiceCall) -> None:
            _LOGGER.info(
                "REMOTE HANDLER CALLED: %s.%s data=%s", domain, service, dict(call.data)
            )
            entity_id_raw = call.data.get("entity_id", "")

            # Normalise: service data can carry a single string or a list.
            if isinstance(entity_id_raw, list):
                entity_ids: list[str] = entity_id_raw
            elif entity_id_raw:
                entity_ids = [entity_id_raw]
            else:
                entity_ids = []

            if not entity_ids:
                _LOGGER.warning(
                    "Remote handler for %s.%s called without entity_id — ignoring",
                    domain,
                    service,
                )
                return

            entity_map: dict[str, str] = self._entity_entry_map()
            clients: dict = self.hass.data.get(DATA_WORKER_CLIENTS, {})

            for eid in entity_ids:
                entry_id = entity_map.get(eid)
                if entry_id is None:
                    # Not a remote entity — skip silently so native HA handling
                    # is not disrupted for locally-managed entities.
                    _LOGGER.debug(
                        "entity_id=%s is not remote, skipping %s.%s routing",
                        eid,
                        domain,
                        service,
                    )
                    continue

                client = clients.get(entry_id)
                if client is None:
                    _LOGGER.error(
                        "No WorkerClient for entry_id=%s (entity_id=%s, %s.%s)",
                        entry_id,
                        eid,
                        domain,
                        service,
                    )
                    continue

                service_data = {k: v for k, v in call.data.items() if k != "entity_id"}
                _LOGGER.debug(
                    "Routing %s.%s for %s -> worker %s", domain, service, eid, entry_id
                )
                await client.call_service(domain, service, eid, service_data)

        return _remote_handler

    # ------------------------------------------------------------------
    # gRPC handlers
    # ------------------------------------------------------------------

    async def SetState(self, request, context):
        """Handle SetState from remote integration.

        Besides updating hass.states, we record the entity_id -> entry_id
        mapping so that the remote service router can find the right worker.

        The worker may inject well-known ``_ha_*`` meta-attributes that are
        consumed here and stripped before the state is stored:

        * ``_ha_friendly_name``  – stored as ``friendly_name`` in the state
          attributes so the UI shows a human-readable label.
        * ``_ha_unique_id``      – registered in the entity registry so HA
          can track the entity across restarts and show it in the UI.
        * ``_ha_device_info``    – JSON-encoded device info dict used to
          create / update the device registry entry and link the entity to
          its device.
        """
        import json

        entity_id: str = request.entity_id
        entry_id: str = request.entry_id  # may be empty for legacy callers

        raw_attrs: dict[str, str] = dict(request.attributes)

        _LOGGER.debug(
            "gRPC SetState: %s = %s (attrs: %s, entry_id: %s)",
            entity_id,
            request.state,
            raw_attrs,
            entry_id,
        )

        # ------------------------------------------------------------------
        # Extract and consume _ha_* meta-attributes injected by the worker.
        # ------------------------------------------------------------------
        ha_friendly_name: str | None = raw_attrs.pop("_ha_friendly_name", None)
        ha_unique_id: str | None = raw_attrs.pop("_ha_unique_id", None)
        ha_device_info_json: str | None = raw_attrs.pop("_ha_device_info", None)

        # ------------------------------------------------------------------
        # Step 1 — Register unique_id in the entity registry FIRST so that
        # the canonical entity_id is known before we push to hass.states.
        # This prevents HA from appending _2 when the entity_id already
        # exists in hass.states but not yet in the registry.
        # ------------------------------------------------------------------
        if ha_unique_id:
            try:
                import homeassistant.helpers.entity_registry as er_module

                entity_registry = er_module.async_get(self.hass)
                domain = (
                    entity_id.split(".", maxsplit=1)[0]
                    if "." in entity_id
                    else entity_id
                )

                platform_name = ""
                if entry_id:
                    entry = self.hass.config_entries.async_get_entry(entry_id)
                    if entry is not None:
                        platform_name = entry.domain

                if platform_name:
                    existing = entity_registry.async_get_entity_id(
                        domain, platform_name, ha_unique_id
                    )
                    if existing is None:
                        er_entry = entity_registry.async_get_or_create(
                            domain=domain,
                            platform=platform_name,
                            unique_id=ha_unique_id,
                            suggested_object_id=entity_id.split(".", 1)[-1],
                            config_entry=self.hass.config_entries.async_get_entry(
                                entry_id
                            )
                            if entry_id
                            else None,
                            original_name=ha_friendly_name,
                            has_entity_name=True,
                        )
                        _LOGGER.debug(
                            "gRPC SetState: registered unique_id=%s for %s (platform=%s)",
                            ha_unique_id,
                            entity_id,
                            platform_name,
                        )
                        # Use the registry entity_id as canonical
                        if er_entry.entity_id != entity_id:
                            _LOGGER.debug(
                                "gRPC SetState: remapping entity_id %s → %s",
                                entity_id,
                                er_entry.entity_id,
                            )
                            entity_id = er_entry.entity_id
                    # Already registered — use the registry entity_id
                    elif existing != entity_id:
                        _LOGGER.debug(
                            "gRPC SetState: remapping entity_id %s → %s (existing registry entry)",
                            entity_id,
                            existing,
                        )
                        entity_id = existing
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "gRPC SetState: could not register unique_id for %s: %s",
                    entity_id,
                    err,
                )

        # ------------------------------------------------------------------
        # Step 2 — Push state to hass.states using the canonical entity_id.
        # ------------------------------------------------------------------
        # Build the final attribute dict that will be stored in hass.states.
        # We promote friendly_name to a real state attribute (HA convention).
        final_attrs: dict[str, str] = raw_attrs
        if ha_friendly_name:
            final_attrs["friendly_name"] = ha_friendly_name

        self.hass.states.async_set(
            entity_id,
            request.state,
            final_attrs,
        )

        # Track which worker owns this entity so the service router can find it.
        if entry_id:
            self._entity_entry_map()[entity_id] = entry_id

        # ------------------------------------------------------------------
        # Problem 3 — register / update device entry and link entity to it.
        # ------------------------------------------------------------------
        if ha_device_info_json:
            try:
                device_info_dict = json.loads(ha_device_info_json)

                import homeassistant.helpers.device_registry as dr_module

                device_registry = dr_module.async_get(self.hass)

                # Re-construct identifiers: stored as [[domain, id], …]
                raw_identifiers = device_info_dict.get("identifiers", [])
                identifiers: set[tuple[str, str]] = {
                    tuple(item)
                    for item in raw_identifiers  # type: ignore[misc]
                }

                if identifiers:
                    device_entry = device_registry.async_get_or_create(
                        config_entry_id=entry_id or "",
                        identifiers=identifiers,
                        name=device_info_dict.get("name"),
                        manufacturer=device_info_dict.get("manufacturer"),
                        model=device_info_dict.get("model"),
                        configuration_url=device_info_dict.get("configuration_url"),
                        sw_version=device_info_dict.get("sw_version"),
                        hw_version=device_info_dict.get("hw_version"),
                    )
                    _LOGGER.debug(
                        "gRPC SetState: device entry id=%s for %s",
                        device_entry.id,
                        entity_id,
                    )

                    # Link entity to device in the entity registry if we have
                    # both a unique_id and a valid device entry.
                    if ha_unique_id and entry_id:
                        try:
                            import homeassistant.helpers.entity_registry as er_module

                            entity_registry = er_module.async_get(self.hass)
                            domain = (
                                entity_id.split(".")[0]
                                if "." in entity_id
                                else entity_id
                            )
                            entry = self.hass.config_entries.async_get_entry(entry_id)
                            platform_name = entry.domain if entry is not None else ""
                            if platform_name:
                                er_entry = entity_registry.async_get(entity_id)
                                if er_entry is not None:
                                    if er_entry.device_id != device_entry.id:
                                        entity_registry.async_update_entity(
                                            entity_id, device_id=device_entry.id
                                        )
                                        _LOGGER.debug(
                                            "gRPC SetState: linked %s to device %s",
                                            entity_id,
                                            device_entry.id,
                                        )
                                else:
                                    # Entity not yet in registry — create it with
                                    # device_id set from the start so the link is
                                    # established on first SetState.
                                    entry_obj = (
                                        self.hass.config_entries.async_get_entry(
                                            entry_id
                                        )
                                    )
                                    entity_registry.async_get_or_create(
                                        domain=domain,
                                        platform=platform_name,
                                        unique_id=ha_unique_id,
                                        suggested_object_id=entity_id.split(".", 1)[-1],
                                        config_entry=entry_obj,
                                        device_id=device_entry.id,
                                    )
                                    _LOGGER.debug(
                                        "gRPC SetState: created registry entry for %s linked to device %s",
                                        entity_id,
                                        device_entry.id,
                                    )
                        except Exception as err:  # noqa: BLE001
                            _LOGGER.debug(
                                "gRPC SetState: could not link entity to device for %s: %s",
                                entity_id,
                                err,
                            )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "gRPC SetState: could not process device_info for %s: %s",
                    entity_id,
                    err,
                )

        return core_pb2.SetStateResponse(success=True)

    async def GetState(self, request, context):
        """Handle GetState from remote integration."""
        state = self.hass.states.get(request.entity_id)
        if state is None:
            return core_pb2.GetStateResponse(found=False)
        # Support both real hass State objects and plain dicts (POC _StateStore)
        if isinstance(state, dict):
            return core_pb2.GetStateResponse(
                found=True,
                entity_id=request.entity_id,
                state=str(state.get("state", "")),
                attributes={k: str(v) for k, v in state.get("attributes", {}).items()},
                last_updated=int(state.get("last_updated", 0)),
            )
        return core_pb2.GetStateResponse(
            found=True,
            entity_id=state.entity_id,
            state=state.state,
            attributes={k: str(v) for k, v in state.attributes.items()},
            last_updated=int(state.last_updated.timestamp()),
        )

    async def RegisterService(self, request, context):
        """Register a remote service and install a routing handler in hass.services.

        Strategy
        --------
        * We maintain ``hass.data[DATA_REMOTE_SERVICES]``, a dict mapping
          ``(domain, service)`` to the set of entry_ids that have declared
          that service.  This is the authoritative routing table.

        * For services that are **not yet registered** in hass (e.g. a custom
          domain / service from the worker), we install a brand-new handler
          that exclusively routes to the remote worker.

        * For services that are **already registered** natively by HA (e.g.
          ``switch.turn_on``, ``switch.turn_off``), we install a routing-aware
          handler that replaces the native one.  The new handler checks each
          entity_id: if the entity is remote (tracked in DATA_ENTITY_ENTRY) it
          forwards the call to the worker via WorkerClient; otherwise it calls
          the entity method directly on the local entity object.

          NOTE: we do NOT try to delegate back to the original EntityComponent
          handler because that handler resolves entities through the HA entity
          registry — remote entities are not in that registry and would simply
          be ignored.
        """
        domain: str = request.domain
        service: str = request.service
        entry_id: str = request.entry_id

        _LOGGER.info(
            "Remote service registered: %s.%s (entry_id=%s)", domain, service, entry_id
        )

        # 1. Update the routing table.
        remote_services = self._remote_services_map()
        key = (domain, service)
        if key not in remote_services:
            remote_services[key] = set()
        remote_services[key].add(entry_id)

        # 2. Install / update the HA-level service handler.
        # We always install our own routing handler (overwriting any existing
        # native one) so that remote entities are reachable.  The handler is
        # idempotent: re-registering on worker restart is harmless.
        handler = self._make_remote_handler(domain, service)
        self.hass.services.async_register(domain, service, handler)
        _LOGGER.info("Installed remote routing handler for %s.%s", domain, service)

        # Confirm what is actually registered right now
        current = self.hass.services._services.get(domain, {}).get(service)  # noqa: SLF001
        if current is not None:
            _LOGGER.info(
                "Confirmed: %s.%s handler is now: %s (is ours: %s)",
                domain,
                service,
                current.job.target,
                current.job.target is handler,
            )
        else:
            _LOGGER.warning(
                "Handler for %s.%s is None after registration!", domain, service
            )

        # HA components (e.g. switch) may re-register the same service after us
        # (e.g. when a new entity platform is set up). We listen for
        # EVENT_SERVICE_REGISTERED and re-install our handler whenever that
        # happens so remote entities keep working.
        listener_key = f"grpc_service_listener_{domain}_{service}"
        if not self.hass.data.get(listener_key, False):
            from homeassistant.const import EVENT_SERVICE_REGISTERED

            @callback
            def _on_service_registered(event) -> None:
                if (
                    event.data.get("domain") == domain
                    and event.data.get("service") == service
                ):
                    # Only re-install if the current handler is no longer ours.
                    current = self.hass.services._services.get(domain, {}).get(service)  # noqa: SLF001
                    if current is not None and current.job.target is not handler:
                        _LOGGER.info(
                            "Re-installing remote handler for %s.%s (overwritten by HA)",
                            domain,
                            service,
                        )
                        self.hass.services.async_register(domain, service, handler)

            self.hass.bus.async_listen(EVENT_SERVICE_REGISTERED, _on_service_registered)
            self.hass.data[listener_key] = True
            _LOGGER.debug(
                "Watching EVENT_SERVICE_REGISTERED for %s.%s", domain, service
            )

        return core_pb2.RegisterServiceResponse(success=True)

    async def CallServiceOnRemote(self, request, context):
        """Forward a service call to the remote integration (stub for POC)."""
        _LOGGER.debug(
            "CallServiceOnRemote: %s.%s on %s",
            request.domain,
            request.service,
            request.entity_id,
        )
        return core_pb2.CallServiceOnRemoteResponse(success=True)

    async def GetEntry(self, request, context):
        """Return a config entry's domain, config and options to a remote worker."""
        import json
        import os

        entry_id = request.entry_id
        if not entry_id:
            return core_pb2.GetEntryResponse(found=False)

        # Look up the config entry in hass.config_entries
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            _LOGGER.warning("GetEntry: entry_id=%s not found", entry_id)
            return core_pb2.GetEntryResponse(found=False)

        # Determine the source of the integration
        # For now, all built-in integrations are "builtin"
        # Custom components will be handled in Phase 7
        source = "builtin"
        custom_components_path = (
            self.hass.config.config_dir + "/custom_components/" + entry.domain
        )
        if os.path.isdir(custom_components_path):
            source = f"local:{entry.domain}"

        _LOGGER.info(
            "GetEntry: serving entry_id=%s domain=%s source=%s",
            entry_id,
            entry.domain,
            source,
        )

        return core_pb2.GetEntryResponse(
            found=True,
            domain=entry.domain,
            config=json.dumps(dict(entry.data)).encode(),
            options=json.dumps(dict(entry.options)).encode(),
            title=entry.title,
            source=source,
        )

    async def RegisterWorker(self, request, context):
        """Register a remote worker and store its gRPC client in hass.data."""
        from homeassistant.grpc.worker_client import WorkerClient

        entry_id = request.entry_id
        worker_address = request.worker_address

        if not entry_id:
            _LOGGER.error("RegisterWorker called with empty entry_id")
            return core_pb2.RegisterWorkerResponse(
                success=False, error="entry_id must not be empty"
            )

        if not worker_address:
            _LOGGER.error(
                "RegisterWorker called with empty worker_address (entry_id=%s)",
                entry_id,
            )
            return core_pb2.RegisterWorkerResponse(
                success=False, error="worker_address must not be empty"
            )

        # If a client already exists for this entry_id, close it first so we
        # don't leak channels when a worker restarts and re-registers.
        clients: dict[str, WorkerClient] = self.hass.data.setdefault(
            DATA_WORKER_CLIENTS, {}
        )
        existing = clients.get(entry_id)
        if existing is not None:
            _LOGGER.debug(
                "Re-registering worker entry_id=%s — closing previous channel", entry_id
            )
            try:
                await existing.close()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Error closing previous WorkerClient for entry_id=%s: %s",
                    entry_id,
                    err,
                )

        try:
            client = WorkerClient(entry_id, worker_address)
            await client.connect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to connect to worker entry_id=%s at %s: %s",
                entry_id,
                worker_address,
                err,
            )
            return core_pb2.RegisterWorkerResponse(success=False, error=str(err))

        clients[entry_id] = client
        _LOGGER.info(
            "Worker registered: entry_id=%s address=%s", entry_id, worker_address
        )
        return core_pb2.RegisterWorkerResponse(success=True)
