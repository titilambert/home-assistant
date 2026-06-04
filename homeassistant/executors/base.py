"""Base executor interface for Home Assistant horizontal scaling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ExecutorBase(ABC):
    """Abstract base class for integration executors."""

    @abstractmethod
    async def start(
        self,
        entry_ids: list[str],
        core_address: str = "localhost:50051",
        **kwargs: Any,
    ) -> None:
        """Start the integration(s) in the executor."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the integration(s)."""
