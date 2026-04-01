"""Re-export shared HassProxy for the pi_hole remote integration."""

from homeassistant.grpc.hass_proxy import HAConfig, HassProxy, StatesProxy

__all__ = ["HAConfig", "HassProxy", "StatesProxy"]
