"""Base worker class."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from homeassistant.worker.const import WORKER_STATUS_UNAVAILABLE


class BaseWorker(ABC):
    """Abstract base class for all worker types."""

    def __init__(self, hass: HomeAssistant, conf: dict) -> None:
        self._hass = hass
        self._conf = conf
        self._name: str = conf["name"]
        self._worker_type: str = conf["type"]
        self._max_integrations: int | None = conf.get("max_integrations")
        self._status: str = WORKER_STATUS_UNAVAILABLE
        self._active_integrations: int = 0
        self._address: str = ""  # gRPC address, set by subclasses

    @property
    def name(self) -> str:
        return self._name

    @property
    def worker_type(self) -> str:
        return self._worker_type

    @property
    def status(self) -> str:
        return self._status

    @property
    def address(self) -> str:
        return self._address

    @property
    def has_capacity(self) -> bool:
        """Return True if this worker can accept more integrations."""
        if self._max_integrations is None:
            return True
        return self._active_integrations < self._max_integrations

    @property
    def active_integrations(self) -> int:
        return self._active_integrations

    @property
    def max_integrations(self) -> int | None:
        return self._max_integrations

    def increment_integrations(self) -> None:
        self._active_integrations += 1

    def decrement_integrations(self) -> None:
        self._active_integrations = max(0, self._active_integrations - 1)

    async def async_check_reachable(self) -> bool:
        """Check if the worker is reachable via gRPC."""
        if not self._address:
            return False
        try:
            import grpc
            import grpc.aio

            channel = grpc.aio.insecure_channel(self._address)
            await asyncio.wait_for(channel.channel_ready(), timeout=5.0)
            await channel.close()
            return True
        except Exception:
            return False

    @abstractmethod
    async def async_start(self) -> None:
        """Start the worker."""

    @abstractmethod
    async def async_stop(self) -> None:
        """Stop the worker."""

    def to_dict(self) -> dict:
        """Return a dict representation for the config flow."""
        return {
            "name": self._name,
            "type": self._worker_type,
            "status": self._status,
            "address": self._address,
            "active_integrations": self._active_integrations,
            "max_integrations": self._max_integrations,
            "has_capacity": self.has_capacity,
        }
