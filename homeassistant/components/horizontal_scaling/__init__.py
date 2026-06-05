"""Home Assistant Horizontal Scaling component."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.worker.config import (
    CONFIG_SCHEMA as CONFIG_SCHEMA,  # re-export for HA component discovery
    async_setup_workers,
)

__all__ = ["CONFIG_SCHEMA"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the horizontal_scaling component.

    The actual worker setup is performed early in bootstrap.py via
    homeassistant.worker.config.async_setup_workers, so this function
    is intentionally a no-op to avoid starting workers twice.
    """
    _ = async_setup_workers  # imported to satisfy static analysis
    return True
