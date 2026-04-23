"""Worker gRPC server — exposes WorkerService so Core can call services on this worker."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import grpc
import grpc.aio

from homeassistant.grpc.protos import core_pb2, core_pb2_grpc

_LOGGER = logging.getLogger(__name__)

DEFAULT_WORKER_PORT = 50052


class WorkerServiceServicer(core_pb2_grpc.WorkerServiceServicer):
    """Handles CallService RPCs from Core."""

    def __init__(self, services_proxy: Any) -> None:
        self._services = services_proxy  # ServicesProxy instance from remote_hass.py

    async def CallService(self, request, context):
        """Dispatch a service call received from Core to the local handler."""
        domain = request.domain
        service = request.service
        entity_id = request.entity_id
        service_data = dict(request.service_data)

        if entity_id:
            service_data["entity_id"] = entity_id

        _LOGGER.info(
            "Core called service %s.%s entity=%s data=%s",
            domain,
            service,
            entity_id,
            service_data,
        )

        handler = self._services._handlers.get((domain, service))
        if handler is None:
            _LOGGER.warning("No handler for %s.%s", domain, service)
            return core_pb2.WorkerCallServiceResponse(
                success=False,
                error=f"No handler for {domain}.{service}",
            )

        try:
            result = handler(service_data)
            if asyncio.iscoroutine(result):
                await result
            return core_pb2.WorkerCallServiceResponse(success=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Service %s.%s failed: %s", domain, service, err)
            return core_pb2.WorkerCallServiceResponse(
                success=False,
                error=str(err),
            )


class WorkerGrpcServer:
    """gRPC server running in the worker process."""

    def __init__(
        self,
        services_proxy: Any,
        port: int = DEFAULT_WORKER_PORT,
    ) -> None:
        self.port = port
        self._services = services_proxy
        self._server: grpc.aio.Server | None = None

    async def start(self) -> None:
        """Start the gRPC server and begin accepting connections."""
        self._server = grpc.aio.server()
        servicer = WorkerServiceServicer(self._services)
        core_pb2_grpc.add_WorkerServiceServicer_to_server(servicer, self._server)
        listen_addr = f"[::]:{self.port}"
        self._server.add_insecure_port(listen_addr)
        await self._server.start()
        _LOGGER.info("Worker gRPC server started on port %d", self.port)

    async def stop(self, grace: float = 5.0) -> None:
        """Gracefully stop the gRPC server."""
        if self._server is not None:
            await self._server.stop(grace=grace)
            _LOGGER.info("Worker gRPC server stopped")
