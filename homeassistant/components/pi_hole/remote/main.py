"""Standalone entry point for the Pi-hole remote integration process.

Usage:
    python -m homeassistant.components.pi_hole.remote.main \\
        --host pihole.example.com \\
        --password YOUR_PASSWORD \\
        [--port 50052] \\
        [--core-address localhost:50051] \\
        [--location admin] \\
        [--protocol https]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import grpc.aio

from homeassistant.components.pi_hole.remote import integration_pb2_grpc
from homeassistant.components.pi_hole.remote.grpc_server import IntegrationGrpcServicer

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_LOGGER = logging.getLogger(__name__)


async def run(args: argparse.Namespace) -> None:
    integration_id = f"pihole_{args.host.replace('.', '_')}"

    # 1. Start the integration gRPC server
    server = grpc.aio.server()
    servicer = IntegrationGrpcServicer(core_address=args.core_address)
    integration_pb2_grpc.add_IntegrationServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{args.port}")
    await server.start()
    _LOGGER.info("Integration gRPC server listening on port %d", args.port)

    # 2. Call Initialize on ourselves
    from homeassistant.components.pi_hole.remote import integration_pb2

    init_resp = await servicer.Initialize(
        integration_pb2.InitRequest(
            integration_id=integration_id,
            config={
                "host": args.host,
                "password": args.password,
                "location": args.location,
                "protocol": args.protocol,
                "ssl": "true" if args.protocol == "https" else "false",
            },
        ),
        context=None,
    )

    if not init_resp.success:
        _LOGGER.error("Initialization failed: %s", init_resp.error)
        await server.stop(0)
        sys.exit(1)

    _LOGGER.info("Entities registered: %s", list(init_resp.entity_ids))

    # 3. Start polling loop
    await servicer.Start(
        integration_pb2.StartRequest(integration_id=integration_id),
        context=None,
    )

    _LOGGER.info(
        "Pi-hole remote integration running. Core at %s. Press Ctrl+C to stop.",
        args.core_address,
    )

    try:
        await server.wait_for_termination()
    except (KeyboardInterrupt, asyncio.CancelledError):
        _LOGGER.info("Shutting down...")
        await servicer.Stop(
            integration_pb2.StopRequest(integration_id=integration_id),
            context=None,
        )
        await server.stop(grace=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pi-hole remote integration service")
    parser.add_argument("--host", required=True, help="Pi-hole hostname or IP")
    parser.add_argument("--password", required=True, help="Pi-hole password / app-password")
    parser.add_argument("--location", default="admin", help="Pi-hole web location (default: admin)")
    parser.add_argument("--protocol", default="https", choices=["http", "https"])
    parser.add_argument("--port", type=int, default=50052, help="gRPC port for this service (default: 50052)")
    parser.add_argument("--core-address", default="localhost:50051", help="HA Core gRPC address (default: localhost:50051)")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
