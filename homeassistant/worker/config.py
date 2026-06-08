"""Worker configuration — reads workers: from configuration.yaml."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.helpers import config_validation as cv

from .const import (
    DATA_WORKER_REGISTRY,
    WORKER_TYPE_DOCKER,
    WORKER_TYPE_KUBERNETES,
    WORKER_TYPE_PROCESS,
    WORKER_TYPE_REMOTE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "workers"

CONF_WORKER_NAME = "name"
CONF_WORKER_TYPE = "type"
CONF_WORKER_PORT = "port"
CONF_WORKER_MAX_INTEGRATIONS = "max_integrations"
CONF_WORKER_ADDRESS = "address"
CONF_WORKER_IMAGE = "image"
CONF_WORKER_HOST = "host"
CONF_WORKER_NAMESPACE = "namespace"
CONF_WORKER_POD_SPEC = "pod_spec"
CONF_WORKER_RESOURCES = "resources"
CONF_WORKER_RESOURCES_CPU = "cpu"
CONF_WORKER_RESOURCES_MEMORY = "memory"
CONF_WORKER_RESOURCES_CPU_SHARES = "cpu_shares"
CONF_WORKER_STOP_ON_SHUTDOWN = "stop_on_shutdown"

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
        vol.Optional(CONF_WORKER_STOP_ON_SHUTDOWN, default=True): cv.boolean,
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
        DOMAIN: vol.All(
            cv.ensure_list,
            [_validate_worker],
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_workers(hass: HomeAssistant, config: dict) -> None:
    """Set up workers from configuration."""
    from .registry import WorkerRegistry  # noqa: PLC0415

    workers_conf: list[dict] = config.get(DOMAIN, [])

    if not workers_conf:
        return

    names = [w[CONF_WORKER_NAME] for w in workers_conf]
    if len(names) != len(set(names)):
        _LOGGER.error("Duplicate worker names in workers configuration")
        return

    registry = WorkerRegistry(hass, workers_conf)
    hass.data[DATA_WORKER_REGISTRY] = registry
    await registry.async_start()

    async def _stop_workers(_event=None) -> None:
        await registry.async_stop()

    hass.bus.async_listen_once("homeassistant_stop", _stop_workers)

    _LOGGER.info(
        "Workers: %d worker(s) declared (%s)",
        len(workers_conf),
        ", ".join(
            f"{w[CONF_WORKER_NAME]} ({w[CONF_WORKER_TYPE]})" for w in workers_conf
        ),
    )
