"""Worker gRPC client — used by Core to call services on remote workers."""

from __future__ import annotations

import logging
from typing import Any

import grpc
import grpc.aio

_LOGGER = logging.getLogger(__name__)


class WorkerClient:
    """gRPC client to call services on a remote worker."""

    def __init__(self, entry_id: str, worker_address: str) -> None:
        """Initialize the worker client."""
        self.entry_id = entry_id
        self.worker_address = worker_address
        self._channel: grpc.aio.Channel | None = None
        self._stub: Any | None = None

    async def connect(self) -> None:
        """Open the gRPC channel and create the stub."""
        from homeassistant.core_grpc.protos import core_pb2_grpc  # noqa: PLC0415

        self._channel = grpc.aio.insecure_channel(self.worker_address)
        self._stub = core_pb2_grpc.WorkerServiceStub(self._channel)  # type: ignore[no-untyped-call]
        _LOGGER.debug(
            "WorkerClient connected to %s (entry_id=%s)",
            self.worker_address,
            self.entry_id,
        )

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        service_data: dict[str, Any],
    ) -> bool:
        """Forward a service call to the remote worker.

        Returns True on success, False on any error.
        """
        from homeassistant.core_grpc.protos import core_pb2  # noqa: PLC0415

        if self._stub is None:
            _LOGGER.error(
                "WorkerClient for entry_id=%s is not connected — call connect() first",
                self.entry_id,
            )
            return False

        try:
            response = await self._stub.CallService(
                core_pb2.WorkerCallServiceRequest(  # type: ignore[attr-defined]
                    domain=domain,
                    service=service,
                    entity_id=entity_id,
                    service_data={k: str(v) for k, v in service_data.items()},
                )
            )
        except grpc.aio.AioRpcError as err:
            _LOGGER.error(
                "gRPC error calling %s.%s on worker %s: [%s] %s",
                domain,
                service,
                self.entry_id,
                err.code(),
                err.details(),
            )
            return False
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Unexpected error calling %s.%s on worker %s: %s",
                domain,
                service,
                self.entry_id,
                err,
            )
            return False
        else:
            if not response.success:
                _LOGGER.warning(
                    "Worker %s returned failure for %s.%s on %s: %s",
                    self.entry_id,
                    domain,
                    service,
                    entity_id,
                    response.error,
                )
            return bool(response.success)

    async def setup_entry(self, entry_id: str) -> bool:
        """Ask the worker to load an integration."""
        from homeassistant.core_grpc.protos import core_pb2  # noqa: PLC0415

        if self._stub is None:
            _LOGGER.error(
                "WorkerClient for entry_id=%s is not connected", self.entry_id
            )
            return False
        try:
            response = await self._stub.SetupEntry(
                core_pb2.WorkerSetupEntryRequest(entry_id=entry_id)  # type: ignore[attr-defined]
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("SetupEntry failed for entry_id=%s: %s", entry_id, err)
            return False
        else:
            return bool(response.success)

    async def teardown_entry(self, entry_id: str) -> bool:
        """Ask the worker to unload an integration."""
        from homeassistant.core_grpc.protos import core_pb2  # noqa: PLC0415

        if self._stub is None:
            return False
        try:
            response = await self._stub.TeardownEntry(
                core_pb2.WorkerTeardownEntryRequest(entry_id=entry_id)  # type: ignore[attr-defined]
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("TeardownEntry failed for entry_id=%s: %s", entry_id, err)
            return False
        else:
            return bool(response.success)

    async def close(self) -> None:
        """Close the gRPC channel gracefully."""
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._stub = None
            _LOGGER.debug(
                "WorkerClient disconnected from %s (entry_id=%s)",
                self.worker_address,
                self.entry_id,
            )
