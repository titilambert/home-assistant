"""Remote entry point for the Pi-hole integration (horizontal scaling POC)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys

_LOGGER = logging.getLogger(__name__)


async def _main(core_address: str, entry_id: str, config: dict) -> None:
    """Run the Pi-hole integration as a remote process."""
    _LOGGER.info(
        "Starting Pi-hole remote integration (entry_id=%s, core=%s)",
        entry_id,
        core_address,
    )

    # Import proxy — depends on grpc layer being generated
    from homeassistant.helpers.remote_hass import HomeAssistantGrpcProxy

    hass = HomeAssistantGrpcProxy(core_address=core_address, entry_id=entry_id)

    # Build a minimal ConfigEntry-compatible object
    # We cannot instantiate homeassistant.config_entries.ConfigEntry directly
    # (it has complex internals), so we create a lightweight dataclass-style object.
    entry = _MinimalConfigEntry(entry_id=entry_id, config=config)

    # Import the REAL integration setup — UNCHANGED code
    from homeassistant.components.pi_hole import async_setup_entry

    _LOGGER.info("Calling async_setup_entry for pi_hole...")
    try:
        success = await async_setup_entry(hass, entry)
    except Exception as exc:
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
    args = parser.parse_args()

    config = json.loads(args.config)
    asyncio.run(_main(args.core_address, args.entry_id, config))


if __name__ == "__main__":
    main()
