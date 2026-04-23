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
        """
        entity_id: str = request.entity_id
        entry_id: str = request.entry_id  # may be empty for legacy callers

        _LOGGER.debug(
            "gRPC SetState: %s = %s (attrs: %s, entry_id: %s)",
            entity_id,
            request.state,
            dict(request.attributes),
            entry_id,
        )

        self.hass.states.async_set(
            entity_id,
            request.state,
            dict(request.attributes),
        )

        # Track which worker owns this entity so the service router can find it.
        if entry_id:
            self._entity_entry_map()[entity_id] = entry_id

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
