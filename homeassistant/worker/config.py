"""Worker configuration — reads workers: from configuration.yaml."""

from __future__ import annotations

import logging
import pathlib
import shutil
from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_WORKER_CORE_ADDRESS,
    CONF_WORKER_EXTRA_MANIFESTS,
    CONF_WORKER_INCLUSTER,
    CONF_WORKER_KUBECONFIG,
    CONF_WORKER_MANIFEST,
    CONF_WORKER_SERVICE_TYPE,
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
CONF_PANEL_URL = "workers_panel_url"
DEFAULT_PANEL_URL = "config/workers"

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
        vol.Optional(CONF_WORKER_CORE_ADDRESS, default="localhost:50051"): cv.string,
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
        vol.Required(CONF_WORKER_CORE_ADDRESS): cv.string,
    }
)

REMOTE_WORKER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_WORKER_NAME): cv.string,
        vol.Required(CONF_WORKER_TYPE): vol.In([WORKER_TYPE_REMOTE]),
        vol.Required(CONF_WORKER_ADDRESS): cv.string,
        vol.Optional(CONF_WORKER_MAX_INTEGRATIONS): vol.All(int, vol.Range(min=1)),
        vol.Optional(CONF_WORKER_CORE_ADDRESS, default="localhost:50051"): cv.string,
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
        vol.Optional(CONF_WORKER_INCLUSTER, default=False): cv.boolean,
        vol.Optional(CONF_WORKER_KUBECONFIG): cv.string,
        # service_type: Kubernetes Service type for the worker.
        # If omitted, auto-detected:
        #   - incluster: true  → ClusterIP  (HA runs inside K8s, internal DNS)
        #   - kubeconfig: ...  → NodePort   (HA runs outside K8s, needs external access)
        vol.Optional(CONF_WORKER_SERVICE_TYPE): vol.In(
            ["ClusterIP", "NodePort", "LoadBalancer"]
        ),
        vol.Optional(CONF_WORKER_MANIFEST): cv.string,
        vol.Optional(CONF_WORKER_EXTRA_MANIFESTS, default=[]): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Required(CONF_WORKER_CORE_ADDRESS): cv.string,
        vol.Optional(CONF_WORKER_STOP_ON_SHUTDOWN, default=True): cv.boolean,
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
        vol.Optional(CONF_PANEL_URL, default=DEFAULT_PANEL_URL): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_workers(hass: HomeAssistant, config: dict) -> None:
    """Set up workers from configuration."""
    from .registry import WorkerRegistry  # noqa: PLC0415

    workers_conf: list[dict] = config.get(DOMAIN, [])
    panel_url: str = config.get(CONF_PANEL_URL, DEFAULT_PANEL_URL)
    hass.data["worker_panel_url"] = panel_url

    if workers_conf:
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

    # Copy panel JS to www/ so it's served at /local/workers-panel.js
    _www_dir = pathlib.Path(hass.config.config_dir) / "www"
    _www_dir.mkdir(exist_ok=True)
    _panel_src = pathlib.Path(__file__).parent / "www" / "workers-panel.js"
    _panel_dst = _www_dir / "workers-panel.js"
    if _panel_src.exists():
        shutil.copy2(_panel_src, _panel_dst)
    else:
        _LOGGER.warning("Workers panel JS source not found at %s", _panel_src)

    # Register the custom panel
    from homeassistant.components.panel_custom import (  # noqa: PLC0415
        async_register_panel,
    )

    await async_register_panel(
        hass,
        frontend_url_path=panel_url,
        webcomponent_name="workers-panel",
        sidebar_title="Workers",
        sidebar_icon="mdi:server-network",
        js_url="/local/workers-panel.js",
        require_admin=True,
    )

    # Register the WebSocket API command
    from homeassistant.components.websocket_api import (  # noqa: PLC0415
        ActiveConnection,
        async_register_command,
        async_response,
        websocket_command,
    )

    @websocket_command({"type": "workers/list"})
    @async_response
    async def websocket_list_workers(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict
    ) -> None:
        """Return list of declared workers."""
        registry = hass.data.get(DATA_WORKER_REGISTRY)
        workers: list[dict] = []
        if registry is not None:
            workers.extend(worker.to_dict() for worker in registry.all_workers())
        connection.send_result(msg["id"], {"workers": workers})

    async_register_command(hass, websocket_list_workers)
