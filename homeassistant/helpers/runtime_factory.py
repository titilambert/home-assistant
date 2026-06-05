"""RuntimeFactory — decides whether an integration runs LOCAL or REMOTE."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# hass.data key storing {entry_id: worker_address} for registry-managed workers
DATA_WORKER_ADDRESSES = "runtime_worker_addresses"

# hass.data key storing the runtime mode per entry_id
DATA_RUNTIME_MODE = "runtime_mode"  # dict[entry_id, "local" | "remote"]

# hass.data key set to True when running inside a generic worker process.
# When True, set_runtime_mode() is a no-op so integrations cannot accidentally
# overwrite the forced "local" mode and trigger an infinite worker spawn loop.
DATA_IN_WORKER = "runtime_in_worker"


def get_runtime_mode(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the runtime mode for a config entry.

    Checks hass.data[DATA_RUNTIME_MODE][entry.entry_id].
    Falls back to "local" if not configured.
    """
    modes: dict[str, str] = hass.data.get(DATA_RUNTIME_MODE, {})
    return modes.get(entry.entry_id, "local")


def set_runtime_mode(hass: HomeAssistant, entry_id: str, mode: str) -> None:
    """Set the runtime mode for a config entry.

    No-op when called from inside a generic worker process (DATA_IN_WORKER=True)
    so that integrations cannot accidentally overwrite the forced "local" mode
    and trigger an infinite worker-spawn loop.
    """
    if hass.data.get(DATA_IN_WORKER, False):
        _LOGGER.debug(
            "set_runtime_mode ignored inside worker (entry_id=%s, mode=%s)",
            entry_id,
            mode,
        )
        return
    if DATA_RUNTIME_MODE not in hass.data:
        hass.data[DATA_RUNTIME_MODE] = {}
    hass.data[DATA_RUNTIME_MODE][entry_id] = mode
    _LOGGER.debug("Runtime mode for entry_id=%s set to %s", entry_id, mode)


def is_remote(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Return True if this entry should run in a remote worker."""
    return get_runtime_mode(hass, entry) == "remote"


async def async_setup_remote(
    hass: HomeAssistant,
    entry: ConfigEntry,
    core_address: str = "localhost:50051",
    worker_port: int = 50052,
    worker_address: str | None = None,
    worker: Any | None = None,
) -> bool:
    """Set up remote execution for a config entry.

    Two modes:
    - worker_address provided: the worker is already running (managed by the
      horizontal_scaling registry). Skip subprocess launch and just register
      the entry against that worker address.
    - worker_address is None: launch a new subprocess via ProcessExecutor
      (legacy / standalone behaviour).

    Called by an integration's async_setup_entry when runtime mode is REMOTE.
    """
    if worker_address is not None:
        # Phase 2 path: worker already running, managed by horizontal_scaling.
        # Check capacity before sending SetupEntry.
        if worker is not None:
            if not worker.has_capacity:
                _LOGGER.error(
                    "Worker '%s' is at capacity (%d/%d integrations)",
                    worker.name,
                    worker.active_integrations,
                    worker.max_integrations,
                )
                return False

        # Send SetupEntry to the worker via gRPC so it loads the integration.
        from homeassistant.grpc.worker_client import WorkerClient

        client = WorkerClient(entry.entry_id, worker_address)
        await client.connect()

        try:
            success = await client.setup_entry(entry.entry_id)
            if not success:
                _LOGGER.error(
                    "Worker at %s failed to set up entry_id=%s",
                    worker_address,
                    entry.entry_id,
                )
                await client.close()
                return False
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "SetupEntry failed for entry_id=%s at %s: %s",
                entry.entry_id,
                worker_address,
                err,
            )
            await client.close()
            return False

        # Store client for teardown
        worker_addresses: dict[str, str] = hass.data.setdefault(
            DATA_WORKER_ADDRESSES, {}
        )
        worker_addresses[entry.entry_id] = worker_address

        # Store client in DATA_WORKER_CLIENTS for service routing
        from homeassistant.grpc.services.state_service import DATA_WORKER_CLIENTS

        clients: dict = hass.data.setdefault(DATA_WORKER_CLIENTS, {})
        clients[entry.entry_id] = client

        async def _teardown_worker() -> None:
            try:
                await client.teardown_entry(entry.entry_id)
            except Exception:  # noqa: BLE001
                pass
            await client.close()
            worker_addresses.pop(entry.entry_id, None)
            clients.pop(entry.entry_id, None)
            if worker is not None:
                worker.decrement_integrations()

        entry.async_on_unload(_teardown_worker)
        if worker is not None:
            worker.increment_integrations()

        _LOGGER.info(
            "Remote worker (registry-managed) set up entry_id=%s "
            "(domain=%s, address=%s)",
            entry.entry_id,
            entry.domain,
            worker_address,
        )
        return True

    # No worker_address provided and no registry-managed worker found.
    _LOGGER.error(
        "Cannot set up entry_id=%s (domain=%s) in REMOTE mode: "
        "no worker address provided. Declare a worker in configuration.yaml "
        "under horizontal_scaling.workers.",
        entry.entry_id,
        entry.domain,
    )
    return False


async def async_teardown_remote(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Teardown a remote worker entry (cleanup only, worker lifecycle managed by registry)."""
    _LOGGER.info("Remote entry teardown for entry_id=%s", entry.entry_id)
    return True
