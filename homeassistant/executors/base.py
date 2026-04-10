"""Base executor interface for Home Assistant horizontal scaling."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ExecutorBase(ABC):
    """Abstract base class for integration executors."""

    @abstractmethod
    async def start(self, domain: str, entry_id: str, config: dict) -> None:
        """Start the integration in the executor."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the integration."""
