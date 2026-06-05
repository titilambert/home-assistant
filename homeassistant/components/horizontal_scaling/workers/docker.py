"""Docker worker — manages a container running the generic worker."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from ..const import (
    CONF_WORKER_HOST,
    CONF_WORKER_IMAGE,
    CONF_WORKER_PORT,
    CONF_WORKER_RESOURCES,
    CONF_WORKER_RESOURCES_CPU,
    CONF_WORKER_RESOURCES_CPU_SHARES,
    CONF_WORKER_RESOURCES_MEMORY,
    CONF_WORKER_STOP_ON_SHUTDOWN,
    WORKER_STATUS_RUNNING,
    WORKER_STATUS_UNAVAILABLE,
)
from .base import BaseWorker

_LOGGER = logging.getLogger(__name__)
RETRY_INTERVAL = 30  # seconds


def _sanitize_name(name: str) -> str:
    """Sanitize worker name for use as Docker container name."""
    return "ha_worker_" + re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")


class DockerWorker(BaseWorker):
    """Worker that runs as a Docker container."""

    def __init__(self, hass: HomeAssistant, conf: dict) -> None:
        """Initialize the Docker worker."""
        super().__init__(hass, conf)
        self._docker_host: str = conf[CONF_WORKER_HOST]
        self._image: str = conf[CONF_WORKER_IMAGE]
        self._port: int = conf[CONF_WORKER_PORT]
        self._resources: dict = conf.get(CONF_WORKER_RESOURCES, {})
        self._stop_on_shutdown: bool = conf.get(CONF_WORKER_STOP_ON_SHUTDOWN, True)
        self._container_name: str = _sanitize_name(self._name)
        self._stopping = False
        self._retry_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._client: Any = None  # docker.DockerClient

        # worker_address is deduced from docker host IP + port
        host_ip = self._extract_host_ip(self._docker_host)
        self._address = f"{host_ip}:{self._port}"

    @staticmethod
    def _extract_host_ip(docker_host: str) -> str:
        """Extract IP/hostname from docker host URL.

        Examples:
          tcp://192.168.1.50:2375  → 192.168.1.50
          unix:///var/run/docker.sock → localhost
        """
        if docker_host.startswith("unix://"):
            return "localhost"
        # tcp://host:port
        parts = docker_host.replace("tcp://", "").split(":")
        return parts[0]

    def _get_docker_client(self):
        """Return a docker.DockerClient for this worker's host."""
        import docker  # noqa: PLC0415

        return docker.DockerClient(base_url=self._docker_host)

    async def async_start(self) -> None:
        """Stop any existing container and start a fresh one."""
        self._stopping = False
        await self._hass.async_add_executor_job(self._start_sync)
        if self._status != WORKER_STATUS_RUNNING:
            if not self._stopping:
                self._schedule_retry()
            return
        # Container started — wait for the gRPC worker inside to be reachable
        self._status = WORKER_STATUS_UNAVAILABLE
        _LOGGER.info(
            "Docker worker '%s' container started, waiting for gRPC port %d...",
            self._name,
            self._port,
        )
        asyncio.create_task(self._wait_for_ready())

    def _start_sync(self) -> None:
        """Synchronous Docker operations (run in executor)."""
        try:
            client = self._get_docker_client()
            self._client = client

            # Check if container already exists
            existing = None
            try:
                existing = client.containers.get(self._container_name)
            except Exception:  # noqa: BLE001
                pass  # Container does not exist

            if existing is not None:
                existing.reload()
                if existing.status == "running":
                    # Container is already running — reuse it
                    _LOGGER.info(
                        "Docker worker '%s' container already running, reusing it",
                        self._container_name,
                    )
                    self._status = WORKER_STATUS_RUNNING
                    return
                else:
                    # Container exists but stopped — remove it and recreate
                    _LOGGER.info(
                        "Removing stopped container '%s'", self._container_name
                    )
                    existing.remove()

            # Build resource limits
            kwargs: dict = {
                "name": self._container_name,
                "image": self._image,
                "detach": True,
                "ports": {f"{self._port}/tcp": self._port},
                "environment": {
                    "HA_MODE": "worker",
                    # Use separate env vars to avoid word-splitting issues with
                    # worker names that contain spaces.
                    "HA_WORKER_CORE_ADDRESS": "host.docker.internal:50051",
                    "HA_WORKER_PORT": str(self._port),
                    "HA_WORKER_NAME": self._name,
                },
                "extra_hosts": {"host.docker.internal": "host-gateway"},
            }

            resources = self._resources
            if resources.get(CONF_WORKER_RESOURCES_CPU):
                kwargs["nano_cpus"] = int(
                    float(resources[CONF_WORKER_RESOURCES_CPU]) * 1e9
                )
            if resources.get(CONF_WORKER_RESOURCES_MEMORY):
                kwargs["mem_limit"] = resources[CONF_WORKER_RESOURCES_MEMORY]
            if resources.get(CONF_WORKER_RESOURCES_CPU_SHARES):
                kwargs["cpu_shares"] = resources[CONF_WORKER_RESOURCES_CPU_SHARES]

            _LOGGER.info(
                "Starting Docker container '%s' from image '%s' on port %d",
                self._container_name,
                self._image,
                self._port,
            )
            client.containers.run(**kwargs)
            self._status = WORKER_STATUS_RUNNING
            _LOGGER.info(
                "Docker worker '%s' started (container=%s, address=%s)",
                self._name,
                self._container_name,
                self._address,
            )

        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to start Docker worker '%s': %s",
                self._name,
                err,
                exc_info=True,
            )
            self._status = WORKER_STATUS_UNAVAILABLE

    async def _wait_for_ready(self, timeout: int = 60, interval: float = 2.0) -> None:
        """Poll until the gRPC port is reachable, then mark RUNNING."""
        import time

        deadline = time.monotonic() + timeout
        while not self._stopping and time.monotonic() < deadline:
            reachable = await self.async_check_reachable()
            if reachable:
                self._status = WORKER_STATUS_RUNNING
                _LOGGER.info(
                    "Docker worker '%s' is ready at %s",
                    self._name,
                    self._address,
                )
                self._monitor_task = asyncio.create_task(self._monitor())
                return
            await asyncio.sleep(interval)

        if not self._stopping:
            _LOGGER.error(
                "Docker worker '%s' did not become ready within %ds",
                self._name,
                timeout,
            )
            self._status = WORKER_STATUS_UNAVAILABLE
            self._schedule_retry()

    async def _monitor(self) -> None:
        """Monitor the container and retry if it crashes."""
        while not self._stopping:
            await asyncio.sleep(10)
            try:
                is_running = await self._hass.async_add_executor_job(
                    self._check_running
                )
                if not is_running:
                    if not self._stopping:
                        _LOGGER.warning(
                            "Docker worker '%s' container stopped unexpectedly, "
                            "retrying in %ds",
                            self._name,
                            RETRY_INTERVAL,
                        )
                        self._status = WORKER_STATUS_UNAVAILABLE
                        self._schedule_retry()
                    return
            except Exception:  # noqa: BLE001
                pass

    def _check_running(self) -> bool:
        """Check if the container is running (sync)."""
        try:
            client = self._get_docker_client()
            container = client.containers.get(self._container_name)
            container.reload()
        except Exception:  # noqa: BLE001
            return False
        else:
            return container.status == "running"

    def _schedule_retry(self) -> None:
        if not self._stopping:
            self._retry_task = asyncio.create_task(self._retry())

    async def _retry(self) -> None:
        await asyncio.sleep(RETRY_INTERVAL)
        if not self._stopping:
            await async_start(self)

    async def async_stop(self) -> None:
        """Stop the Docker container."""
        self._stopping = True
        if self._retry_task:
            self._retry_task.cancel()
        if self._stop_on_shutdown:
            await self._hass.async_add_executor_job(self._stop_sync)
        else:
            _LOGGER.info(
                "Docker worker '%s' stop_on_shutdown=false — leaving container running",
                self._name,
            )
            self._status = WORKER_STATUS_UNAVAILABLE

    def _stop_sync(self) -> None:
        """Stop the container synchronously (run in executor)."""
        try:
            client = self._get_docker_client()
            container = client.containers.get(self._container_name)
            _LOGGER.info("Stopping Docker container '%s'", self._container_name)
            container.stop(timeout=10)
            _LOGGER.info("Docker worker '%s' stopped", self._name)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Could not stop Docker container '%s': %s",
                self._container_name,
                err,
            )
        self._status = WORKER_STATUS_UNAVAILABLE


# Fix: use the correct method name for retry
async def async_start(worker: DockerWorker) -> None:
    """Helper to restart a worker (avoids self-reference issue in _retry)."""
    await worker.async_start()
