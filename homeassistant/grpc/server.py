"""CoreGrpcServicer — bridges gRPC calls from remote integrations into HA."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from . import core_pb2, core_pb2_grpc

_LOGGER = logging.getLogger(__name__)


class CoreGrpcServicer(core_pb2_grpc.CoreServiceServicer):
    """gRPC server that exposes HA state machine to remote integrations."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def SetState(self, request, context):
        """Receive a state update from a remote integration and apply it to HA."""
        attributes = dict(request.attributes)
        _LOGGER.debug(
            "gRPC SetState: %s = %s (attrs: %s)",
            request.entity_id,
            request.state,
            attributes,
        )
        self.hass.states.async_set(request.entity_id, request.state, attributes)
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
