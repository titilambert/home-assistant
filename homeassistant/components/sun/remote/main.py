"""Standalone entry point for the Sun remote integration process.

Usage:
    python -m homeassistant.components.sun.remote.main \\
        [--port 50053] \\
        [--core-address localhost:50051]

The process fetches HA location config (lat/lon/timezone) via gRPC, then
computes solar position and events locally using astral, pushing states to
the Core on the same schedule as the built-in Sun entity.
"""

from __future__ import annotations

import asyncio
import argparse
import logging
import sys

import grpc.aio

from homeassistant.components.sun.remote import integration_pb2, integration_pb2_grpc
from homeassistant.components.sun.remote.grpc_server import SunGrpcServicer

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_LOGGER = logging.getLogger(__name__)

INTEGRATION_ID = "sun"


async def run(args: argparse.Namespace) -> None:
    server = grpc.aio.server()
    servicer = SunGrpcServicer(core_address=args.core_address)
    integration_pb2_grpc.add_IntegrationServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{args.port}")
    await server.start()
    _LOGGER.info("Sun integration gRPC server listening on port %d", args.port)

    init_resp = await servicer.Initialize(
        integration_pb2.InitRequest(integration_id=INTEGRATION_ID, config={}),
        context=None,
    )
    if not init_resp.success:
        _LOGGER.error("Initialization failed: %s", init_resp.error)
        await server.stop(0)
        sys.exit(1)

    _LOGGER.info("Entities: %s", list(init_resp.entity_ids))

    await servicer.Start(
        integration_pb2.StartRequest(integration_id=INTEGRATION_ID),
        context=None,
    )

    _LOGGER.info(
        "Sun remote integration running. Core at %s. Press Ctrl+C to stop.",
        args.core_address,
    )

    try:
        await server.wait_for_termination()
    except (KeyboardInterrupt, asyncio.CancelledError):
        _LOGGER.info("Shutting down...")
        await servicer.Stop(
            integration_pb2.StopRequest(integration_id=INTEGRATION_ID),
            context=None,
        )
        await server.stop(grace=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sun remote integration service")
    parser.add_argument("--port", type=int, default=50053)
    parser.add_argument("--core-address", default="localhost:50051")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
