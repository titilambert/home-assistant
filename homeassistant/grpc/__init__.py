"""Home Assistant Core gRPC server for remote integrations."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

GRPC_SERVER_PORT = 50051


async def start_grpc_server(hass: HomeAssistant, port: int = GRPC_SERVER_PORT):
    """Start the Core gRPC server and return the server instance."""
    import grpc.aio

    from homeassistant.grpc import core_pb2_grpc

    from .server import CoreGrpcServicer

    server = grpc.aio.server()
    core_pb2_grpc.add_CoreServiceServicer_to_server(CoreGrpcServicer(hass), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    _LOGGER.info("Core gRPC server started on port %d", port)
    return server
