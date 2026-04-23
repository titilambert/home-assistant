#!/usr/bin/env python3
"""Standalone POC runner for the Home Assistant horizontal scaling proof of concept.

This script validates the full gRPC flow WITHOUT needing to start a real Home Assistant
instance. It:

  1. Spins up a minimal in-process "Core" (CoreGrpcServer backed by a real hass-like
     state store).
  2. Launches the Pi-hole integration in a subprocess via ProcessExecutor.
  3. Waits for state updates to arrive over gRPC.
  4. Prints a summary and exits.

Usage
-----
From the repository root:

    python horizontal_scaling/poc_runner.py \\
        --host 192.168.1.10 \\
        --api-key YOUR_PIHOLE_API_KEY \\
        [--location admin] \\
        [--ssl] \\
        [--port 50051] \\
        [--wait 60]

If you just want to smoke-test the gRPC plumbing without a real Pi-hole, omit
--host / --api-key.  The remote process will fail to connect to Pi-hole but the
gRPC channel itself will still be validated.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys
import time
from typing import Any
import uuid

# ---------------------------------------------------------------------------
# Make sure the repository root is on sys.path so that
# `import homeassistant.*` works when running from horizontal_scaling/.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("poc_runner")

# ---------------------------------------------------------------------------
# Minimal in-memory state store (replaces hass.states for the POC server)
# ---------------------------------------------------------------------------


class _StateStore:
    """Thread/coroutine-safe in-memory state store."""

    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}

    def async_set(
        self,
        entity_id: str,
        state: str,
        attributes: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> None:
        self._states[entity_id] = {
            "state": state,
            "attributes": attributes or {},
            "last_updated": time.time(),
        }
        _LOGGER.info(
            "[Core StateStore] %-50s = %s  attrs=%s",
            entity_id,
            state,
            attributes,
        )

    def get(self, entity_id: str) -> dict[str, Any] | None:
        return self._states.get(entity_id)

    def all(self) -> dict[str, dict[str, Any]]:
        return dict(self._states)


# ---------------------------------------------------------------------------
# Minimal hass-like object (only what CoreGrpcServer / StateService need)
# ---------------------------------------------------------------------------


class _MinimalHass:
    """Bare-minimum hass stand-in for the gRPC server."""

    def __init__(self) -> None:
        self.states = _StateStore()
        self.data: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# POC runner
# ---------------------------------------------------------------------------


async def run_poc(
    host: str | None,
    api_key: str | None,
    location: str,
    ssl: bool,
    verify_ssl: bool,
    grpc_port: int,
    wait_seconds: int,
) -> None:
    """Run the full horizontal-scaling POC flow."""

    # ------------------------------------------------------------------
    # Step 1 – Start the Core gRPC server
    # ------------------------------------------------------------------
    _LOGGER.info("=" * 60)
    _LOGGER.info("Step 1: Starting Core gRPC server on port %d …", grpc_port)

    from homeassistant.grpc.server import CoreGrpcServer

    hass = _MinimalHass()
    server = CoreGrpcServer(hass, port=grpc_port)
    await server.start()
    _LOGGER.info("Core gRPC server is UP.")

    # ------------------------------------------------------------------
    # Step 2 – Build a config dict for Pi-hole (mirrors ConfigEntry.data)
    # ------------------------------------------------------------------
    entry_id = str(uuid.uuid4())

    config: dict[str, Any] = {
        "name": "Pi-hole POC",
        "host": host or "127.0.0.1",  # dummy if no real Pi-hole
        "location": location,
        "ssl": ssl,
        "verify_ssl": verify_ssl,
        "api_key": api_key or "",
    }

    _LOGGER.info("=" * 60)
    _LOGGER.info("Step 2: Config entry")
    _LOGGER.info("  entry_id : %s", entry_id)
    _LOGGER.info("  host     : %s", config["host"])
    _LOGGER.info("  location : %s", config["location"])
    _LOGGER.info("  ssl      : %s", config["ssl"])
    _LOGGER.info("  api_key  : %s", "***" if config["api_key"] else "(none)")

    # ------------------------------------------------------------------
    # Step 3 – Launch Pi-hole integration in a subprocess
    # ------------------------------------------------------------------
    _LOGGER.info("=" * 60)
    _LOGGER.info("Step 3: Launching Pi-hole integration via ProcessExecutor …")

    from homeassistant.executors.process import ProcessExecutor

    executor = ProcessExecutor()
    await executor.start(
        domain="pi_hole",
        entry_id=entry_id,
        config=config,
        core_address=f"localhost:{grpc_port}",
    )
    _LOGGER.info("Subprocess launched (pid visible in stderr stream).")

    # ------------------------------------------------------------------
    # Step 4 – Wait and collect state updates
    # ------------------------------------------------------------------
    _LOGGER.info("=" * 60)
    _LOGGER.info("Step 4: Waiting %d seconds for state updates …", wait_seconds)
    _LOGGER.info("(Watch for [Core StateStore] lines above)")

    deadline = time.monotonic() + wait_seconds
    last_count = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(5)
        current_states = hass.states.all()
        count = len(current_states)
        if count != last_count:
            _LOGGER.info("  → %d entity state(s) received so far.", count)
            last_count = count

    # ------------------------------------------------------------------
    # Step 5 – Print summary
    # ------------------------------------------------------------------
    _LOGGER.info("=" * 60)
    _LOGGER.info("Step 5: Summary")
    final_states = hass.states.all()
    if final_states:
        _LOGGER.info("Received %d state(s):", len(final_states))
        for entity_id, info in sorted(final_states.items()):
            _LOGGER.info(
                "  %-50s  state=%-10s  attrs=%s",
                entity_id,
                info["state"],
                info["attributes"],
            )
    else:
        _LOGGER.warning(
            "No states received. Possible reasons:\n"
            "  • Pi-hole host unreachable (%s)\n"
            "  • Wrong API key\n"
            "  • gRPC connection refused (check port %d)\n"
            "  • Subprocess crashed (check stderr above)",
            config["host"],
            grpc_port,
        )

    # ------------------------------------------------------------------
    # Step 6 – Shutdown
    # ------------------------------------------------------------------
    _LOGGER.info("=" * 60)
    _LOGGER.info("Step 6: Shutting down …")
    await executor.stop()
    await server.stop()
    _LOGGER.info("Done.")

    # Success if at least one state was received
    if not final_states:
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Horizontal Scaling POC runner for the Pi-hole integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Pi-hole hostname or IP address (e.g. 192.168.1.10)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Pi-hole API key / password",
    )
    parser.add_argument(
        "--location",
        default="admin",
        help="Pi-hole web interface location (default: admin)",
    )
    parser.add_argument(
        "--ssl",
        action="store_true",
        default=False,
        help="Use HTTPS to connect to Pi-hole",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        default=False,
        help="Disable TLS certificate verification",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=50051,
        help="gRPC port for the Core server (default: 50051)",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=60,
        help="Seconds to wait for state updates (default: 60)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG logging",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    _LOGGER.info("Home Assistant Horizontal Scaling — POC Runner")
    _LOGGER.info("Repository root: %s", REPO_ROOT)

    asyncio.run(
        run_poc(
            host=args.host,
            api_key=args.api_key,
            location=args.location,
            ssl=args.ssl,
            verify_ssl=not args.no_verify_ssl,
            grpc_port=args.port,
            wait_seconds=args.wait,
        )
    )


if __name__ == "__main__":
    main()
