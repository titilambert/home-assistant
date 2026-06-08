"""Worker gRPC server — exposes WorkerService so Core can call services on this worker."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import grpc
import grpc.aio

from homeassistant.core_grpc.protos import core_pb2, core_pb2_grpc

_LOGGER = logging.getLogger(__name__)

DEFAULT_WORKER_PORT = 50052


class WorkerServiceServicer(core_pb2_grpc.WorkerServiceServicer):
    """Handles WorkerService RPCs from Core."""

    def __init__(self, services_proxy: Any, hass_proxy: Any = None) -> None:
        self._services = services_proxy  # ServicesProxy instance from worker/proxy.py
        self._hass = hass_proxy  # HomeAssistantGrpcProxy instance
        self._entries: dict[str, Any] = {}  # entry_id -> _MinimalConfigEntry

    async def SetupEntry(self, request, context):
        """Load an integration into this worker (called by Core in persistent mode)."""
        entry_id = request.entry_id
        _LOGGER.info("Core requested SetupEntry for entry_id=%s", entry_id)

        if self._hass is None:
            return core_pb2.WorkerSetupEntryResponse(
                success=False, error="Worker has no hass proxy"
            )

        if entry_id in self._entries:
            _LOGGER.debug("Entry %s already loaded", entry_id)
            return core_pb2.WorkerSetupEntryResponse(success=True)

        try:
            from homeassistant.worker.main import _setup_integration

            entry = await _setup_integration(self._hass, self._hass._stub, entry_id)
            if entry is None:
                return core_pb2.WorkerSetupEntryResponse(
                    success=False,
                    error=f"async_setup_entry failed for entry_id={entry_id}",
                )
            self._entries[entry_id] = entry
            # Register with Core so service calls are routed here
            worker_address = (
                f"localhost:{self._port if hasattr(self, '_port') else 50052}"
            )
            await self._hass._stub.RegisterWorker(
                core_pb2.RegisterWorkerRequest(
                    entry_id=entry_id,
                    worker_address=worker_address,
                )
            )
            return core_pb2.WorkerSetupEntryResponse(success=True)
        except Exception as err:
            _LOGGER.exception("SetupEntry failed for entry_id=%s", entry_id)
            return core_pb2.WorkerSetupEntryResponse(success=False, error=str(err))

    async def TeardownEntry(self, request, context):
        """Unload an integration from this worker (called by Core)."""
        entry_id = request.entry_id
        _LOGGER.info("Core requested TeardownEntry for entry_id=%s", entry_id)
        entry = self._entries.pop(entry_id, None)
        if entry is not None:
            await entry.async_unload()
        return core_pb2.WorkerTeardownEntryResponse(success=True)

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
        hass_proxy: Any = None,
    ) -> None:
        self.port = port
        self._services = services_proxy
        self._hass = hass_proxy
        self._server: grpc.aio.Server | None = None
        self._servicer: WorkerServiceServicer | None = None

    async def start(self) -> None:
        """Start the gRPC server and begin accepting connections."""
        self._server = grpc.aio.server()
        self._servicer = WorkerServiceServicer(self._services, self._hass)
        self._servicer._port = self.port  # give servicer access to the port
        core_pb2_grpc.add_WorkerServiceServicer_to_server(self._servicer, self._server)
        listen_addr = f"[::]:{self.port}"
        self._server.add_insecure_port(listen_addr)
        await self._server.start()
        _LOGGER.info("Worker gRPC server started on port %d", self.port)

    async def stop(self, grace: float = 5.0) -> None:
        """Gracefully stop the gRPC server."""
        if self._server is not None:
            await self._server.stop(grace=grace)
            _LOGGER.info("Worker gRPC server stopped")
