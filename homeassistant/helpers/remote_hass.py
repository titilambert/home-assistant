"""Backward compatibility shim — use homeassistant.worker.proxy instead."""

from homeassistant.worker.proxy import (  # noqa: F401
    HomeAssistantGrpcProxy,
    _apply_patches,
    patch_integration_namespace,
)
