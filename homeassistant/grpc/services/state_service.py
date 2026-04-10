"""State service gRPC implementation for the Core gRPC server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from homeassistant.grpc.protos import core_pb2, core_pb2_grpc

_LOGGER = logging.getLogger(__name__)


class CoreServiceServicer(core_pb2_grpc.CoreServiceServicer):
    """gRPC servicer that handles requests from remote integrations."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the servicer."""
        self.hass = hass

    async def SetState(self, request, context):
        """Handle SetState from remote integration."""
        _LOGGER.debug(
            "gRPC SetState: %s = %s (attrs: %s)",
            request.entity_id,
            request.state,
            dict(request.attributes),
        )
        self.hass.states.async_set(
            request.entity_id,
            request.state,
            dict(request.attributes),
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
        """Track that a service was registered in a remote integration."""
        _LOGGER.info(
            "Remote service registered: %s.%s (entry_id=%s)",
            request.domain,
            request.service,
            request.entry_id,
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
