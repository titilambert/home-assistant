"""ProcessExecutor: runs integrations as a subprocess using the generic worker."""

from __future__ import annotations

import asyncio
import logging
import sys

from .base import ExecutorBase

_LOGGER = logging.getLogger(__name__)


class ProcessExecutor(ExecutorBase):
    """Executes integrations in a separate Python subprocess using the generic worker."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._entry_ids: list[str] = []
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None

    async def start(
        self,
        entry_ids: list[str],
        core_address: str = "localhost:50051",
        worker_port: int = 50052,
        **kwargs,
    ) -> None:
        """Start the generic worker subprocess for the given entry_ids."""
        self._entry_ids = entry_ids
        cmd = [sys.executable, "-m", "homeassistant.worker.main"]
        cmd += ["--core-address", core_address]
        cmd += ["--worker-port", str(worker_port)]
        for entry_id in entry_ids:
            cmd += ["--entry-id", entry_id]

        _LOGGER.info(
            "Starting generic worker for entries %s: %s",
            entry_ids,
            " ".join(cmd),
        )
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stdout_task = asyncio.create_task(
            self._stream_output(self._process.stdout, "[worker/stdout]")
        )
        self._stderr_task = asyncio.create_task(
            self._stream_output(self._process.stderr, "[worker/stderr]")
        )
        _LOGGER.info(
            "Generic worker started (pid=%s) for entries %s",
            self._process.pid,
            entry_ids,
        )

    async def stop(self) -> None:
        """Stop the worker subprocess."""
        if self._process is not None:
            _LOGGER.info("Stopping generic worker (entries=%s)", self._entry_ids)
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except TimeoutError:
                _LOGGER.warning("Force-killing generic worker")
                self._process.kill()
            self._process = None
        for task in (self._stdout_task, self._stderr_task):
            if task is not None:
                task.cancel()

    @staticmethod
    async def _stream_output(stream, prefix: str) -> None:
        if stream is None:
            return
        async for line in stream:
            _LOGGER.info("%s %s", prefix, line.decode().rstrip())
