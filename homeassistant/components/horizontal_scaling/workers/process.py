"""Process worker — runs a local subprocess."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from ..const import CONF_WORKER_PORT, WORKER_STATUS_RUNNING, WORKER_STATUS_UNAVAILABLE
from .base import BaseWorker

_LOGGER = logging.getLogger(__name__)
RETRY_INTERVAL = 30  # seconds


class ProcessWorker(BaseWorker):
    """Worker that runs as a local subprocess."""

    def __init__(self, hass, conf: dict) -> None:
        super().__init__(hass, conf)
        self._port: int = conf[CONF_WORKER_PORT]
        self._address = f"localhost:{self._port}"
        self._process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._retry_task: asyncio.Task | None = None
        self._stopping = False

    async def async_start(self) -> None:
        """Launch the worker subprocess."""
        self._stopping = False
        await self._launch()

    async def _launch(self) -> None:
        """Launch the subprocess and start monitoring it."""
        cmd = [
            sys.executable,
            "-m",
            "homeassistant.worker.main",
            "--core-address",
            "localhost:50051",
            "--worker-port",
            str(self._port),
            "--worker-name",
            self._name,
        ]
        _LOGGER.info("Starting process worker '%s': %s", self._name, " ".join(cmd))
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._stdout_task = asyncio.create_task(
                self._stream(self._process.stdout, "stdout")
            )
            self._stderr_task = asyncio.create_task(
                self._stream(self._process.stderr, "stderr")
            )
            self._status = WORKER_STATUS_RUNNING
            _LOGGER.info(
                "Process worker '%s' started (pid=%s, port=%s)",
                self._name,
                self._process.pid,
                self._port,
            )
            # Monitor process and retry on crash
            asyncio.create_task(self._monitor())
        except Exception as err:
            _LOGGER.error("Failed to start process worker '%s': %s", self._name, err)
            self._status = WORKER_STATUS_UNAVAILABLE
            self._schedule_retry()

    async def _monitor(self) -> None:
        """Wait for process to exit and retry if not stopping."""
        if self._process:
            await self._process.wait()
            if not self._stopping:
                _LOGGER.warning(
                    "Process worker '%s' exited unexpectedly, retrying in %ds",
                    self._name,
                    RETRY_INTERVAL,
                )
                self._status = WORKER_STATUS_UNAVAILABLE
                self._schedule_retry()

    def _schedule_retry(self) -> None:
        if not self._stopping:
            self._retry_task = asyncio.create_task(self._retry())

    async def _retry(self) -> None:
        await asyncio.sleep(RETRY_INTERVAL)
        if not self._stopping:
            await self._launch()

    async def async_stop(self) -> None:
        """Kill the worker subprocess."""
        self._stopping = True
        if self._retry_task:
            self._retry_task.cancel()
        if self._process:
            _LOGGER.info("Stopping process worker '%s'", self._name)
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except TimeoutError:
                self._process.kill()
            self._process = None
        for task in (self._stdout_task, self._stderr_task):
            if task:
                task.cancel()
        self._status = WORKER_STATUS_UNAVAILABLE

    async def _stream(self, stream, label: str) -> None:
        if stream is None:
            return
        async for line in stream:
            _LOGGER.info("[%s/%s] %s", self._name, label, line.decode().rstrip())
