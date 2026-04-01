"""Standalone entry point for the Pi-hole remote integration process.

Usage:
    python -m homeassistant.components.pi_hole.remote.main \\
        [--core-address localhost:50051] \\
        [--port 50052]

All Pi-hole configuration (host, password, SSL, …) is fetched from the
Home Assistant Core via gRPC — no local config needed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys

import grpc.aio

from homeassistant.grpc import integration_pb2, integration_pb2_grpc
from homeassistant.components.pi_hole.remote.grpc_server import IntegrationGrpcServicer

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_LOGGER = logging.getLogger(__name__)


def _own_address(port: int) -> str:
    """Return a reachable address for this process (used to register with Core)."""
    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
    except OSError:
        ip = "127.0.0.1"
    return f"{ip}:{port}"


async def run(args: argparse.Namespace) -> None:
    own_address = _own_address(args.port)

    server = grpc.aio.server()
    servicer = IntegrationGrpcServicer(
        core_address=args.core_address,
        own_grpc_address=own_address,
    )
    integration_pb2_grpc.add_IntegrationServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{args.port}")
    await server.start()
    _LOGGER.info("Pi-hole integration gRPC server on port %d (advertising %s)", args.port, own_address)

    init_resp = await servicer.Initialize(
        integration_pb2.InitRequest(integration_id="pi_hole", config={}),
        context=None,
    )
    if not init_resp.success:
        _LOGGER.error("Initialization failed: %s", init_resp.error)
        await server.stop(0)
        sys.exit(1)

    await servicer.Start(
        integration_pb2.StartRequest(integration_id="pi_hole"),
        context=None,
    )

    _LOGGER.info("Running. Core at %s. Ctrl+C to stop.", args.core_address)
    try:
        await server.wait_for_termination()
    except (KeyboardInterrupt, asyncio.CancelledError):
        _LOGGER.info("Shutting down…")
        await servicer.Stop(
            integration_pb2.StopRequest(integration_id="pi_hole"),
            context=None,
        )
        await server.stop(grace=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pi-hole remote integration service")
    parser.add_argument("--core-address", default="localhost:50051")
    parser.add_argument("--port", type=int, default=50052)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
