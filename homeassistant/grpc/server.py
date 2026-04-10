"""Core gRPC server for Home Assistant horizontal scaling POC."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import grpc
import grpc.aio

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from homeassistant.grpc.protos import core_pb2_grpc
from homeassistant.grpc.services.state_service import CoreServiceServicer

_LOGGER = logging.getLogger(__name__)

DEFAULT_GRPC_PORT = 50051


class CoreGrpcServer:
    """Minimal gRPC server exposing Core APIs to remote integrations."""

    def __init__(self, hass: HomeAssistant, port: int = DEFAULT_GRPC_PORT) -> None:
        """Initialize the gRPC server."""
        self.hass = hass
        self.port = port
        self._server: grpc.aio.Server | None = None

    async def start(self) -> None:
        """Start the gRPC server."""
        self._server = grpc.aio.server()
        servicer = CoreServiceServicer(self.hass)
        core_pb2_grpc.add_CoreServiceServicer_to_server(servicer, self._server)
        listen_addr = f"[::]:{self.port}"
        self._server.add_insecure_port(listen_addr)
        await self._server.start()
        _LOGGER.info("Core gRPC server started on port %s", self.port)

    async def stop(self, grace: float = 5.0) -> None:
        """Stop the gRPC server."""
        if self._server is not None:
            await self._server.stop(grace=grace)
            _LOGGER.info("Core gRPC server stopped")
