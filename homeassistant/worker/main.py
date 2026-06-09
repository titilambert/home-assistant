"""Generic remote worker for Home Assistant horizontal scaling."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys
from typing import Any

# Make sure the repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger(__name__)


class _MinimalConfigEntry:
    """Lightweight stand-in for homeassistant.config_entries.ConfigEntry."""

    def __init__(
        self,
        entry_id: str,
        domain: str,
        title: str,
        config: dict,
        options: dict,
    ) -> None:
        from homeassistant.config_entries import ConfigEntryState

        self.entry_id = entry_id
        self.unique_id = entry_id  # use entry_id as unique_id for the worker
        self.domain = domain
        self.title = title
        self.data = config
        self.options = options
        self.runtime_data = None
        self.pref_disable_polling = False
        self.state = ConfigEntryState.SETUP_IN_PROGRESS
        self._on_unload: list = []

    def async_on_unload(self, func) -> None:
        """Register a callback to be called on unload."""
        self._on_unload.append(func)

    def add_update_listener(self, listener) -> Any:
        """Register a listener for option updates (no-op in worker)."""
        # Options don't change in the worker — return a no-op cancel function
        return lambda: None

    def async_create_background_task(self, hass, target, name, eager_start=True):
        """Create a background task."""
        return asyncio.ensure_future(target)

    async def async_unload(self) -> None:
        """Run all registered unload callbacks."""
        for func in reversed(self._on_unload):
            result = func()
            if hasattr(result, "__await__"):
                await result
        self._on_unload.clear()

    def __setattr__(self, name, value):
        super().__setattr__(name, value)


async def _setup_integration(hass, stub, entry_id: str) -> _MinimalConfigEntry | None:
    """Fetch config from Core and set up one integration."""
    from homeassistant.core_grpc.protos import core_pb2

    _LOGGER.info("Fetching config for entry_id=%s", entry_id)
    response = await stub.GetEntry(core_pb2.GetEntryRequest(entry_id=entry_id))

    if not response.found:
        _LOGGER.error("Entry not found in Core: entry_id=%s", entry_id)
        return None

    domain = response.domain
    config = json.loads(response.config) if response.config else {}
    options = json.loads(response.options) if response.options else {}
    title = response.title
    source = response.source

    _LOGGER.info(
        "Setting up integration: domain=%s entry_id=%s title=%s source=%s",
        domain,
        entry_id,
        title,
        source,
    )

    # Import the integration module
    import importlib

    try:
        module_name = f"homeassistant.components.{domain}"
        module = importlib.import_module(module_name)
    except ImportError as err:
        _LOGGER.error("Cannot import integration %s: %s", domain, err)
        return None

    # Patch namespace after import
    from homeassistant.worker.proxy import patch_integration_namespace

    patch_integration_namespace(module_name)

    # Create the config entry
    entry = _MinimalConfigEntry(
        entry_id=entry_id,
        domain=domain,
        title=title,
        config=config,
        options=options,
    )

    # Call async_setup_entry
    if not hasattr(module, "async_setup_entry"):
        _LOGGER.error("Integration %s has no async_setup_entry", domain)
        return None

    try:
        success = await module.async_setup_entry(hass, entry)
    except Exception:
        _LOGGER.exception(
            "async_setup_entry failed for %s (entry_id=%s)", domain, entry_id
        )
        return None

    if not success:
        _LOGGER.error(
            "async_setup_entry returned False for %s (entry_id=%s)", domain, entry_id
        )
        return None

    _LOGGER.info("Integration %s (entry_id=%s) running successfully", domain, entry_id)
    return entry


async def _main(
    core_address: str, entry_ids: list[str], worker_port: int, worker_name: str = ""
) -> None:
    """Run the generic worker."""
    _LOGGER.info(
        "Generic worker starting (core=%s, entries=%s)", core_address, entry_ids
    )

    # Create the shared hass proxy
    from homeassistant.worker.proxy import HomeAssistantGrpcProxy

    hass = HomeAssistantGrpcProxy(core_address=core_address, entry_id="")

    # Mark this process as a worker so that set_runtime_mode() becomes a no-op
    # and integrations cannot accidentally trigger an infinite worker-spawn loop.
    from homeassistant.helpers.runtime_factory import DATA_IN_WORKER

    hass.data[DATA_IN_WORKER] = True

    # Fetch real HA config from Core
    await hass.async_fetch_config()

    # Start the worker gRPC server
    from homeassistant.core_grpc.worker_server import WorkerGrpcServer

    worker_server = WorkerGrpcServer(hass.services, port=worker_port, hass_proxy=hass)
    await worker_server.start()

    # Register the worker with Core (persistent mode — no entry_id yet)
    if not entry_ids and worker_name:
        from homeassistant.core_grpc.protos import core_pb2 as _pb2

        worker_address = f"localhost:{worker_port}"
        try:
            await hass._stub.RegisterWorker(
                _pb2.RegisterWorkerRequest(
                    entry_id=f"__worker__{worker_name}",
                    worker_address=worker_address,
                )
            )
            _LOGGER.info(
                "Worker '%s' registered with Core at %s",
                worker_name,
                worker_address,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not register with Core: %s", err)

    # Set up each integration
    from homeassistant.core_grpc.protos import core_pb2

    entries = []
    for entry_id in entry_ids:
        entry = await _setup_integration(hass, hass._stub, entry_id)
        if entry is not None:
            entries.append(entry)
            # Register this worker with the Core for this entry_id
            worker_address = f"localhost:{worker_port}"
            await hass._stub.RegisterWorker(
                core_pb2.RegisterWorkerRequest(
                    entry_id=entry_id,
                    worker_address=worker_address,
                )
            )
            _LOGGER.info(
                "Registered with Core as worker for entry_id=%s at %s",
                entry_id,
                worker_address,
            )

    if entry_ids and not entries:
        _LOGGER.error("No integrations were set up successfully — exiting")
        await worker_server.stop()
        await hass.close()
        sys.exit(1)

    if entries:
        _LOGGER.info(
            "Worker running with %d integration(s): %s",
            len(entries),
            [e.domain for e in entries],
        )
    else:
        _LOGGER.info(
            "Worker running in persistent mode on port %d — waiting for SetupEntry calls from Core",
            worker_port,
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

    _LOGGER.info("Shutting down worker...")
    for entry in entries:
        await entry.async_unload()
    await worker_server.stop()
    await hass.close()
    _LOGGER.info("Worker stopped.")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Home Assistant Generic Remote Worker",
    )
    parser.add_argument(
        "--core-address",
        default=os.environ.get("HA_WORKER_CORE_ADDRESS", "localhost:50051"),
        help="gRPC address of the HA Core server (default: localhost:50051, env: HA_WORKER_CORE_ADDRESS)",
    )
    parser.add_argument(
        "--entry-id",
        action="append",
        dest="entry_ids",
        default=[],
        help=(
            "Config entry ID to load (can be repeated for multiple integrations). "
            "If omitted, the worker starts in persistent mode and waits for "
            "SetupEntry calls from Core."
        ),
    )
    parser.add_argument(
        "--worker-port",
        type=int,
        default=int(os.environ.get("HA_WORKER_PORT", "50052")),
        help="Port for the worker gRPC server (default: 50052, env: HA_WORKER_PORT)",
    )
    parser.add_argument(
        "--worker-name",
        default=os.environ.get("HA_WORKER_NAME", ""),
        help="Name of this worker (used for registration with Core in persistent mode, env: HA_WORKER_NAME)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    asyncio.run(
        _main(args.core_address, args.entry_ids, args.worker_port, args.worker_name)
    )


if __name__ == "__main__":
    main()
