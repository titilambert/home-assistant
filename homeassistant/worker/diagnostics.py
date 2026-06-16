"""Diagnostics for remote worker integrations."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_WORKER_REGISTRY


async def async_get_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a remote integration config entry.

    Can be imported by integrations that run on a remote worker and want
    to expose worker information in their diagnostics.
    """
    worker_name: str = entry.data.get("worker_name", "")
    runtime_mode: str = entry.data.get("runtime_mode", "local")

    diag: dict[str, Any] = {
        "runtime_mode": runtime_mode,
        "worker_name": worker_name,
    }

    if runtime_mode == "remote" and worker_name:
        registry = hass.data.get(DATA_WORKER_REGISTRY)
        if registry is not None:
            worker = registry.get_worker(worker_name)
            if worker is not None:
                diag["worker"] = worker.to_dict()

    return diag
