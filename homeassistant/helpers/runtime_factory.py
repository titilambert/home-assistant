"""RuntimeFactory — decides whether an integration runs LOCAL or REMOTE."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# hass.data key storing {entry_id: ProcessExecutor}
DATA_EXECUTORS = "runtime_executors"

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
) -> bool:
    """Launch a ProcessExecutor for the given config entry.

    Called by an integration's async_setup_entry when runtime mode is REMOTE.
    """
    from homeassistant.executors.process import ProcessExecutor

    executors: dict[str, ProcessExecutor] = hass.data.setdefault(DATA_EXECUTORS, {})

    # Stop existing executor if any (e.g. on reload)
    existing = executors.get(entry.entry_id)
    if existing is not None:
        _LOGGER.debug("Stopping existing executor for entry_id=%s", entry.entry_id)
        await existing.stop()

    executor = ProcessExecutor()
    await executor.start(
        entry_ids=[entry.entry_id],
        core_address=core_address,
        worker_port=worker_port,
    )
    executors[entry.entry_id] = executor

    # Register cleanup on entry unload
    async def _stop_executor() -> None:
        await executor.stop()
        executors.pop(entry.entry_id, None)

    entry.async_on_unload(_stop_executor)

    _LOGGER.info(
        "Remote worker started for entry_id=%s (domain=%s)",
        entry.entry_id,
        entry.domain,
    )
    return True


async def async_teardown_remote(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop the ProcessExecutor for the given config entry."""
    executors: dict = hass.data.get(DATA_EXECUTORS, {})
    executor = executors.pop(entry.entry_id, None)
    if executor is not None:
        await executor.stop()
        _LOGGER.info("Remote worker stopped for entry_id=%s", entry.entry_id)
    return True
