"""Placeholder for worker types not yet implemented (docker, kubernetes)."""

from __future__ import annotations

import logging

from homeassistant.worker.const import WORKER_STATUS_NOT_IMPLEMENTED
from homeassistant.worker.workers.base import BaseWorker

_LOGGER = logging.getLogger(__name__)


class NotImplementedWorker(BaseWorker):
    """Placeholder for Docker and Kubernetes workers (coming in Phase 4/5)."""

    async def async_start(self) -> None:
        """Mark worker as not-implemented and log a warning."""
        self._status = WORKER_STATUS_NOT_IMPLEMENTED
        _LOGGER.warning(
            "Worker '%s' type '%s' is not yet implemented (Phase 4/5). Skipping.",
            self._name,
            self._conf["type"],
        )

    async def async_stop(self) -> None:
        """Stop the worker (no-op for unimplemented types)."""
