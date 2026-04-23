"""Remote entry point for the Pi-hole integration (horizontal scaling POC)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys

_LOGGER = logging.getLogger(__name__)


async def _main(
    core_address: str, entry_id: str, config: dict, worker_port: int = 50052
) -> None:
    """Run the Pi-hole integration as a remote process."""
    _LOGGER.info(
        "Starting Pi-hole remote integration (entry_id=%s, core=%s)",
        entry_id,
        core_address,
    )

    # Import proxy — depends on grpc layer being generated
    from homeassistant.helpers.remote_hass import HomeAssistantGrpcProxy

    hass = HomeAssistantGrpcProxy(core_address=core_address, entry_id=entry_id)

    # Start worker gRPC server
    from homeassistant.grpc.worker_server import WorkerGrpcServer

    worker_server = WorkerGrpcServer(hass.services, port=worker_port)
    await worker_server.start()

    # Register with Core
    from homeassistant.grpc.protos import core_pb2

    worker_address = f"localhost:{worker_port}"
    await hass._stub.RegisterWorker(
        core_pb2.RegisterWorkerRequest(
            entry_id=entry_id,
            worker_address=worker_address,
        )
    )
    _LOGGER.info("Registered with Core as worker at %s", worker_address)

    # Build a minimal ConfigEntry-compatible object
    # We cannot instantiate homeassistant.config_entries.ConfigEntry directly
    # (it has complex internals), so we create a lightweight dataclass-style object.
    entry = _MinimalConfigEntry(entry_id=entry_id, config=config)

    # Import the REAL integration setup — UNCHANGED code.
    # The integration module must be imported BEFORE patching its namespace,
    # so that it is present in sys.modules when patch_integration_namespace()
    # walks its attributes.
    from homeassistant.components.pi_hole import async_setup_entry
    from homeassistant.helpers.remote_hass import patch_integration_namespace

    # Fix already-bound references inside pi_hole (e.g. async_get_clientsession
    # imported at module level via `from ... import async_get_clientsession`).
    patch_integration_namespace("homeassistant.components.pi_hole")

    _LOGGER.info("Calling async_setup_entry for pi_hole...")
    try:
        success = await async_setup_entry(hass, entry)
    except Exception:
        _LOGGER.exception("async_setup_entry raised an exception")
        sys.exit(1)

    if not success:
        _LOGGER.error("async_setup_entry returned False — aborting")
        sys.exit(1)

    _LOGGER.info(
        "Pi-hole integration running remotely. Polling every 5 minutes. Press Ctrl+C to stop."
    )

    # Keep running until interrupted
    stop_event = asyncio.Event()

    def _handle_signal(*_):
        _LOGGER.info("Signal received, shutting down...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    await stop_event.wait()

    _LOGGER.info("Stopping worker gRPC server...")
    await worker_server.stop()
    _LOGGER.info("Closing gRPC connection...")
    await hass.close()
    _LOGGER.info("Pi-hole remote integration stopped.")


class _MinimalConfigEntry:
    """Lightweight stand-in for homeassistant.config_entries.ConfigEntry.

    Only exposes the attributes that the Pi-hole integration actually reads.
    """

    def __init__(self, entry_id: str, config: dict) -> None:
        from homeassistant.const import CONF_NAME

        self.entry_id = entry_id
        self.domain = "pi_hole"
        self.title = config.get(CONF_NAME, "Pi-hole")
        self.data = config
        self.options: dict = {}
        self.runtime_data = None  # Will be populated by async_setup_entry
        self.pref_disable_polling = False  # Allow coordinator to schedule refreshes
        from homeassistant.config_entries import ConfigEntryState

        self.state = ConfigEntryState.SETUP_IN_PROGRESS
        self._on_unload: list = []

    def async_create_background_task(self, hass, target, name, eager_start=True):
        """Create a background asyncio task for the coordinator refresh loop."""
        import asyncio

        return asyncio.ensure_future(target)

    def async_on_unload(self, func) -> None:
        """Register a function to call when the entry is unloaded."""
        self._on_unload.append(func)

    async def async_unload(self) -> None:
        """Call all registered unload callbacks."""
        for func in reversed(self._on_unload):
            result = func()
            if hasattr(result, "__await__"):
                await result
        self._on_unload.clear()

    def __setattr__(self, name: str, value) -> None:
        # Allow runtime_data to be set by the integration
        super().__setattr__(name, value)


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Run Pi-hole integration as a remote process"
    )
    parser.add_argument(
        "--core-address",
        default="localhost:50051",
        help="gRPC address of the Home Assistant Core server (default: localhost:50051)",
    )
    parser.add_argument("--entry-id", required=True, help="Config entry ID")
    parser.add_argument(
        "--config",
        required=True,
        help="JSON-encoded config dict (same as ConfigEntry.data)",
    )
    parser.add_argument(
        "--worker-port",
        type=int,
        default=50052,
        help="Port for the worker gRPC server (default: 50052)",
    )
    args = parser.parse_args()

    config = json.loads(args.config)
    asyncio.run(_main(args.core_address, args.entry_id, config, args.worker_port))


if __name__ == "__main__":
    main()
