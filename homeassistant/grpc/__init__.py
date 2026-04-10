"""Home Assistant Core gRPC package — horizontal scaling POC."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .server import CoreGrpcServer

_LOGGER = logging.getLogger(__name__)

DATA_GRPC_SERVER = "grpc_server"


async def async_start_grpc_server(
    hass: HomeAssistant, port: int = 50051
) -> "CoreGrpcServer":
    """Start the Core gRPC server and store it in hass.data.

    This is called once during Home Assistant startup.
    The server listens on *localhost* only (insecure, POC).
    """
    from .server import CoreGrpcServer

    if DATA_GRPC_SERVER in hass.data:
        _LOGGER.debug("Core gRPC server already running — skipping")
        return hass.data[DATA_GRPC_SERVER]

    server = CoreGrpcServer(hass, port=port)
    await server.start()
    hass.data[DATA_GRPC_SERVER] = server

    async def _stop_grpc_server(_event=None) -> None:
        await server.stop()
        hass.data.pop(DATA_GRPC_SERVER, None)

    hass.bus.async_listen_once(
        "homeassistant_stop",  # EVENT_HOMEASSISTANT_STOP
        _stop_grpc_server,
    )

    _LOGGER.info("Core gRPC server started on port %d", port)
    return server
