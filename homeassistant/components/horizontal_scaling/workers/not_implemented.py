"""Placeholder for worker types not yet implemented (docker, kubernetes)."""

from __future__ import annotations

import logging

from ..const import CONF_WORKER_TYPE, WORKER_STATUS_NOT_IMPLEMENTED
from .base import BaseWorker

_LOGGER = logging.getLogger(__name__)


class NotImplementedWorker(BaseWorker):
    """Placeholder for Docker and Kubernetes workers (coming in Phase 4/5)."""

    async def async_start(self) -> None:
        self._status = WORKER_STATUS_NOT_IMPLEMENTED
        _LOGGER.warning(
            "Worker '%s' type '%s' is not yet implemented (Phase 4/5). Skipping.",
            self._name,
            self._conf[CONF_WORKER_TYPE],
        )

    async def async_stop(self) -> None:
        pass
