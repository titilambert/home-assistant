"""ProcessExecutor: runs an integration as a subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from .base import ExecutorBase

_LOGGER = logging.getLogger(__name__)


class ProcessExecutor(ExecutorBase):
    """Executes an integration in a separate Python subprocess."""

    def __init__(self) -> None:
        """Initialize the ProcessExecutor."""
        self._process: asyncio.subprocess.Process | None = None
        self._domain: str | None = None
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None

    async def start(
        self,
        domain: str,
        entry_id: str,
        config: dict,
        core_address: str = "localhost:50051",
    ) -> None:
        """Start integration in subprocess."""
        self._domain = domain
        cmd = [
            sys.executable,
            "-m",
            f"homeassistant.components.{domain}.remote.main",
            "--core-address",
            core_address,
            "--entry-id",
            entry_id,
            "--config",
            json.dumps(config),
        ]
        _LOGGER.info("Starting remote integration %s: %s", domain, " ".join(cmd))
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Stream subprocess logs
        self._stdout_task = asyncio.create_task(
            self._stream_output(self._process.stdout, f"[{domain}/stdout]")
        )
        self._stderr_task = asyncio.create_task(
            self._stream_output(self._process.stderr, f"[{domain}/stderr]")
        )
        _LOGGER.info(
            "Remote integration %s started (pid=%s)", domain, self._process.pid
        )

    async def stop(self) -> None:
        """Stop the subprocess."""
        if self._process is not None:
            _LOGGER.info("Stopping remote integration %s", self._domain)
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except TimeoutError:
                _LOGGER.warning("Force-killing remote integration %s", self._domain)
                self._process.kill()
            self._process = None
        for task in (self._stdout_task, self._stderr_task):
            if task is not None:
                task.cancel()

    @staticmethod
    async def _stream_output(stream, prefix: str) -> None:
        """Read and log subprocess output line by line."""
        if stream is None:
            return
        async for line in stream:
            _LOGGER.debug("%s %s", prefix, line.decode().rstrip())
