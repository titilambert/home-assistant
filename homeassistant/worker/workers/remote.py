"""Remote worker — connects to an already-running worker."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.worker.const import WORKER_STATUS_RUNNING, WORKER_STATUS_UNAVAILABLE
from homeassistant.worker.workers.base import BaseWorker

_LOGGER = logging.getLogger(__name__)
RETRY_INTERVAL = 30


class RemoteWorker(BaseWorker):
    """Worker that connects to an already-running remote process."""

    def __init__(self, hass, conf: dict) -> None:
        super().__init__(hass, conf)
        self._address = conf["address"]
        self._retry_task: asyncio.Task | None = None
        self._stopping = False

    async def async_start(self) -> None:
        """Try to connect to the remote worker."""
        self._stopping = False
        await self._connect()

    async def _connect(self) -> None:
        reachable = await self.async_check_reachable()
        if reachable:
            self._status = WORKER_STATUS_RUNNING
            _LOGGER.info(
                "Remote worker '%s' connected at %s", self._name, self._address
            )
        else:
            self._status = WORKER_STATUS_UNAVAILABLE
            _LOGGER.warning(
                "Remote worker '%s' not reachable at %s, retrying in %ds",
                self._name,
                self._address,
                RETRY_INTERVAL,
            )
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        if not self._stopping:
            self._retry_task = asyncio.create_task(self._retry())

    async def _retry(self) -> None:
        await asyncio.sleep(RETRY_INTERVAL)
        if not self._stopping:
            await self._connect()

    async def async_stop(self) -> None:
        """Nothing to do — the remote worker keeps running."""
        self._stopping = True
        if self._retry_task:
            self._retry_task.cancel()
        self._status = WORKER_STATUS_UNAVAILABLE
        _LOGGER.info(
            "Remote worker '%s' disconnected (still running remotely)", self._name
        )
