"""Home Assistant Core gRPC package — horizontal scaling POC."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .server import CoreGrpcServer

_LOGGER = logging.getLogger(__name__)

DATA_GRPC_SERVER = "grpc_server"


def _import_grpc_modules() -> None:
    """Pre-import grpc and protobuf modules in a thread to avoid blocking the event loop.

    grpc and protobuf use importlib.import_module internally (e.g. for the C extension
    google._upb._message), which is a blocking I/O-like operation that must not run
    directly in the asyncio event loop thread.
    """
    import grpc
    import grpc.aio  # noqa: F401

    from homeassistant.core_grpc.protos import (
        core_pb2,  # noqa: F401
        core_pb2_grpc,  # noqa: F401
    )
    from homeassistant.core_grpc.server import CoreGrpcServer  # noqa: F401
    from homeassistant.core_grpc.services.state_service import (
        CoreServiceServicer,  # noqa: F401
    )


async def async_start_grpc_server(
    hass: HomeAssistant, port: int = 50051
) -> CoreGrpcServer:
    """Start the Core gRPC server and store it in hass.data.

    This is called once during Home Assistant startup.
    The server listens on *localhost* only (insecure, POC).

    All blocking imports (grpc, protobuf C extensions) are performed in an
    executor thread before the server is instantiated, so the event loop is
    never blocked.
    """
    if DATA_GRPC_SERVER in hass.data:
        _LOGGER.debug("Core gRPC server already running — skipping")
        return hass.data[DATA_GRPC_SERVER]

    # Run blocking imports in a thread pool executor so the event loop is not
    # blocked by importlib.import_module calls inside grpc / protobuf.
    await hass.async_add_executor_job(_import_grpc_modules)

    # Now that all modules are cached in sys.modules, these imports are instant.
    from .server import CoreGrpcServer

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
