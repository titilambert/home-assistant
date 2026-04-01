"""Config flow to configure the Sun integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_RUNTIME_MODE,
    DEFAULT_NAME,
    DOMAIN,
    RUNTIME_MODE_LOCAL,
    RUNTIME_MODE_REMOTE,
)


class SunOptionsFlowHandler(OptionsFlow):
    """Handle Sun options (runtime mode selection)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_mode = self.config_entry.options.get(CONF_RUNTIME_MODE, RUNTIME_MODE_LOCAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_RUNTIME_MODE, default=current_mode): vol.In(
                        [RUNTIME_MODE_LOCAL, RUNTIME_MODE_REMOTE]
                    ),
                }
            ),
        )


class SunConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Sun."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SunOptionsFlowHandler:
        return SunOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        if user_input is not None:
            return self.async_create_entry(title=DEFAULT_NAME, data={})

        return self.async_show_form(step_id="user")

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle import from configuration.yaml."""
        return await self.async_step_user(import_data)
