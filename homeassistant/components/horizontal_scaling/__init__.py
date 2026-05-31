"""Home Assistant Horizontal Scaling component."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    CONF_WORKERS,
    CONF_WORKER_NAME,
    CONF_WORKER_TYPE,
    CONF_WORKER_PORT,
    CONF_WORKER_MAX_INTEGRATIONS,
    CONF_WORKER_ADDRESS,
    CONF_WORKER_IMAGE,
    CONF_WORKER_HOST,
    CONF_WORKER_NAMESPACE,
    CONF_WORKER_POD_SPEC,
    CONF_WORKER_RESOURCES,
    CONF_WORKER_RESOURCES_CPU,
    CONF_WORKER_RESOURCES_MEMORY,
    CONF_WORKER_RESOURCES_CPU_SHARES,
    WORKER_TYPE_PROCESS,
    WORKER_TYPE_DOCKER,
    WORKER_TYPE_REMOTE,
    WORKER_TYPE_KUBERNETES,
    DATA_WORKER_REGISTRY,
)

_LOGGER = logging.getLogger(__name__)

# Schema for resource limits (docker + kubernetes)
RESOURCES_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_WORKER_RESOURCES_CPU): str,
        vol.Optional(CONF_WORKER_RESOURCES_MEMORY): str,
        vol.Optional(CONF_WORKER_RESOURCES_CPU_SHARES): int,
    }
)

# Schema per worker type
PROCESS_WORKER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_WORKER_NAME): cv.string,
        vol.Required(CONF_WORKER_TYPE): vol.In([WORKER_TYPE_PROCESS]),
        vol.Required(CONF_WORKER_PORT): cv.port,
        vol.Optional(CONF_WORKER_MAX_INTEGRATIONS): vol.All(int, vol.Range(min=1)),
    }
)

DOCKER_WORKER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_WORKER_NAME): cv.string,
        vol.Required(CONF_WORKER_TYPE): vol.In([WORKER_TYPE_DOCKER]),
        vol.Required(CONF_WORKER_HOST): cv.string,
        vol.Required(CONF_WORKER_IMAGE): cv.string,
        vol.Required(CONF_WORKER_PORT): cv.port,
        vol.Optional(CONF_WORKER_MAX_INTEGRATIONS): vol.All(int, vol.Range(min=1)),
        vol.Optional(CONF_WORKER_RESOURCES): RESOURCES_SCHEMA,
    }
)

REMOTE_WORKER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_WORKER_NAME): cv.string,
        vol.Required(CONF_WORKER_TYPE): vol.In([WORKER_TYPE_REMOTE]),
        vol.Required(CONF_WORKER_ADDRESS): cv.string,
        vol.Optional(CONF_WORKER_MAX_INTEGRATIONS): vol.All(int, vol.Range(min=1)),
    }
)

KUBERNETES_WORKER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_WORKER_NAME): cv.string,
        vol.Required(CONF_WORKER_TYPE): vol.In([WORKER_TYPE_KUBERNETES]),
        vol.Required(CONF_WORKER_NAMESPACE): cv.string,
        vol.Required(CONF_WORKER_IMAGE): cv.string,
        vol.Required(CONF_WORKER_PORT): cv.port,
        vol.Optional(CONF_WORKER_MAX_INTEGRATIONS): vol.All(int, vol.Range(min=1)),
        vol.Optional(CONF_WORKER_POD_SPEC): dict,
    }
)


# Dispatcher that validates each worker against the right schema
def _validate_worker(worker: dict) -> dict:
    worker_type = worker.get(CONF_WORKER_TYPE)
    schemas = {
        WORKER_TYPE_PROCESS: PROCESS_WORKER_SCHEMA,
        WORKER_TYPE_DOCKER: DOCKER_WORKER_SCHEMA,
        WORKER_TYPE_REMOTE: REMOTE_WORKER_SCHEMA,
        WORKER_TYPE_KUBERNETES: KUBERNETES_WORKER_SCHEMA,
    }
    schema = schemas.get(worker_type)
    if schema is None:
        raise vol.Invalid(f"Unknown worker type: {worker_type}")
    return schema(worker)


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_WORKERS, default=[]): vol.All(
                    cv.ensure_list,
                    [_validate_worker],
                ),
            }
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the horizontal_scaling component."""
    conf = config.get(DOMAIN, {})
    workers_conf: list[dict] = conf.get(CONF_WORKERS, [])

    # Check for duplicate worker names
    names = [w[CONF_WORKER_NAME] for w in workers_conf]
    if len(names) != len(set(names)):
        _LOGGER.error("Duplicate worker names in horizontal_scaling configuration")
        return False

    # Import and initialise the worker registry
    from .worker_registry import WorkerRegistry

    registry = WorkerRegistry(hass, workers_conf)
    hass.data[DATA_WORKER_REGISTRY] = registry

    # Start all declared workers
    await registry.async_start()

    # Register shutdown callback
    async def _stop_workers(_event=None) -> None:
        await registry.async_stop()

    hass.bus.async_listen_once("homeassistant_stop", _stop_workers)

    _LOGGER.info(
        "Horizontal scaling: %d worker(s) declared (%s)",
        len(workers_conf),
        ", ".join(
            f"{w[CONF_WORKER_NAME]} ({w[CONF_WORKER_TYPE]})" for w in workers_conf
        ),
    )
    return True
