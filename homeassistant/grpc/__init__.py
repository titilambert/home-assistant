"""Home Assistant Core gRPC server for remote integrations."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "remote_integration_server"

CONF_BINDINGS = "bindings"
CONF_PORT = "port"

DEFAULT_BINDINGS = ["::"]  # all interfaces (IPv4 + IPv6)
DEFAULT_PORT = 50051

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_BINDINGS, default=DEFAULT_BINDINGS): vol.All(
                    cv.ensure_list, [cv.string]
                ),
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def start_grpc_server(
    hass: HomeAssistant,
    bindings: list[str] | None = None,
    port: int = DEFAULT_PORT,
):
    """Start the Core gRPC server and return the server instance."""
    import grpc.aio

    from homeassistant.grpc import core_pb2_grpc
    from .server import CoreGrpcServicer

    if bindings is None:
        bindings = DEFAULT_BINDINGS

    server = grpc.aio.server()
    core_pb2_grpc.add_CoreServiceServicer_to_server(CoreGrpcServicer(hass), server)

    for address in bindings:
        listen_addr = f"{address}:{port}"
        server.add_insecure_port(listen_addr)
        _LOGGER.info("Core gRPC server binding on %s", listen_addr)

    await server.start()
    return server
