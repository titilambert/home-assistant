"""CoreGrpcServicer — bridges gRPC calls from remote integrations into HA."""

from __future__ import annotations

import logging

import grpc.aio

from homeassistant.core import HomeAssistant, callback

from . import core_pb2, core_pb2_grpc, integration_pb2_grpc

_LOGGER = logging.getLogger(__name__)

EVENT_GRPC_INTEGRATION_REGISTERED = "grpc_integration_registered"


class CoreGrpcServicer(core_pb2_grpc.CoreServiceServicer):
    """gRPC server that exposes HA state machine to remote integrations."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def SetState(self, request, context):
        """Receive a state update from a remote integration and apply it to HA."""
        _LOGGER.debug("gRPC SetState: %s = %s", request.entity_id, request.state)
        self.hass.states.async_set(
            request.entity_id, request.state, dict(request.attributes)
        )
        return core_pb2.SetStateResponse(success=True)

    async def GetState(self, request, context):
        """Return current HA state for a given entity."""
        state = self.hass.states.get(request.entity_id)
        if state is None:
            return core_pb2.GetStateResponse(found=False)
        return core_pb2.GetStateResponse(
            found=True,
            entity_id=state.entity_id,
            state=state.state,
            attributes={k: str(v) for k, v in state.attributes.items()},
            last_updated=int(state.last_updated.timestamp()),
        )

    async def GetConfig(self, request, context):
        """Return HA core configuration (location, timezone) to remote integrations."""
        cfg = self.hass.config
        return core_pb2.GetConfigResponse(
            latitude=cfg.latitude,
            longitude=cfg.longitude,
            elevation=cfg.elevation,
            time_zone=str(cfg.time_zone),
            location_name=cfg.location_name,
        )

    async def GetIntegrationConfig(self, request, context):
        """Return config entry data for a given domain to the remote integration process."""
        entries = self.hass.config_entries.async_entries(request.domain)
        if not entries:
            return core_pb2.GetIntegrationConfigResponse(found=False)
        entry = entries[0]
        # Serialize entry.data values as strings for the proto map<string,string>
        data = {k: str(v) for k, v in entry.data.items()}
        return core_pb2.GetIntegrationConfigResponse(
            found=True,
            entry_id=entry.entry_id,
            data=data,
        )

    async def RegisterIntegration(self, request, context):
        """Register a remote integration process: store its gRPC stub and notify HA."""
        _LOGGER.info(
            "RegisterIntegration: domain=%s id=%s address=%s entities=%s",
            request.domain,
            request.integration_id,
            request.grpc_address,
            list(request.entity_ids),
        )

        channel = grpc.aio.insecure_channel(request.grpc_address)
        stub = integration_pb2_grpc.IntegrationServiceStub(channel)

        stubs: dict = self.hass.data.setdefault("grpc_stubs", {})
        stubs[request.domain] = stub

        # Fire event so integrations set up in REMOTE mode can react
        # (e.g. activate proxy switch entities)
        @callback
        def _fire():
            self.hass.bus.async_fire(
                EVENT_GRPC_INTEGRATION_REGISTERED,
                {
                    "domain": request.domain,
                    "integration_id": request.integration_id,
                    "grpc_address": request.grpc_address,
                    "entity_ids": list(request.entity_ids),
                    "switch_entity_ids": list(request.switch_entity_ids),
                },
            )

        self.hass.loop.call_soon_threadsafe(_fire)

        return core_pb2.RegisterIntegrationResponse(success=True)
