"""Worker registry — tracks declared workers, their status and capacity."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .const import (
    CONF_WORKER_NAME,
    CONF_WORKER_TYPE,
    CONF_WORKER_MAX_INTEGRATIONS,
    WORKER_TYPE_PROCESS,
    WORKER_TYPE_DOCKER,
    WORKER_TYPE_REMOTE,
    WORKER_TYPE_KUBERNETES,
    WORKER_STATUS_RUNNING,
    WORKER_STATUS_UNAVAILABLE,
    WORKER_STATUS_NOT_IMPLEMENTED,
)

_LOGGER = logging.getLogger(__name__)


class WorkerRegistry:
    """Registry of all declared workers."""

    def __init__(self, hass: HomeAssistant, workers_conf: list[dict]) -> None:
        self._hass = hass
        self._workers: dict[str, BaseWorker] = {}
        self._conf = workers_conf
        self._build_workers()

    def _build_workers(self) -> None:
        from .workers.process import ProcessWorker
        from .workers.remote import RemoteWorker
        from .workers.not_implemented import NotImplementedWorker

        for conf in self._conf:
            name = conf[CONF_WORKER_NAME]
            worker_type = conf[CONF_WORKER_TYPE]

            if worker_type == WORKER_TYPE_PROCESS:
                worker = ProcessWorker(self._hass, conf)
            elif worker_type == WORKER_TYPE_REMOTE:
                worker = RemoteWorker(self._hass, conf)
            elif worker_type in (WORKER_TYPE_DOCKER, WORKER_TYPE_KUBERNETES):
                worker = NotImplementedWorker(self._hass, conf)
            else:
                _LOGGER.error("Unknown worker type: %s", worker_type)
                continue

            self._workers[name] = worker

    async def async_start(self) -> None:
        """Start all workers."""
        await asyncio.gather(
            *(worker.async_start() for worker in self._workers.values()),
            return_exceptions=True,
        )

    async def async_stop(self) -> None:
        """Stop all workers."""
        await asyncio.gather(
            *(worker.async_stop() for worker in self._workers.values()),
            return_exceptions=True,
        )

    def get_available_workers(self) -> list[BaseWorker]:
        """Return workers that are running and have capacity."""
        return [
            w
            for w in self._workers.values()
            if w.status == WORKER_STATUS_RUNNING and w.has_capacity
        ]

    def get_worker(self, name: str) -> BaseWorker | None:
        """Return a worker by name."""
        return self._workers.get(name)

    def all_workers(self) -> list[BaseWorker]:
        """Return all declared workers."""
        return list(self._workers.values())
