"""Config and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_CLOUD_HOST,
    CONF_CLOUD_PORT,
    CONF_DEVICE_IP,
    CONF_LISTEN_PORT,
    CONF_ON_VALUE,
    DEFAULT_CLOUD_PORT,
    DEFAULT_LISTEN_PORT,
    DEFAULT_ON_VALUE,
    DOMAIN,
)


class RshSocketConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the socket, its cloud endpoint and the port to listen on."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None
                              ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_DEVICE_IP] = (user_input.get(CONF_DEVICE_IP) or "").strip()
            await self.async_set_unique_id(
                user_input[CONF_DEVICE_IP] or f"{user_input[CONF_CLOUD_HOST]}:"
                                              f"{user_input[CONF_LISTEN_PORT]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get("name") or user_input[CONF_CLOUD_HOST],
                data=user_input,
            )

        schema = vol.Schema({
            vol.Required("name", default="Smart socket"): str,
            # Optional on purpose: with hairpin NAT the connection arrives from
            # the router, not from the socket, so pinning the IP would reject it.
            vol.Optional(CONF_DEVICE_IP, default=""): str,
            vol.Required(CONF_CLOUD_HOST): str,
            vol.Required(CONF_CLOUD_PORT, default=DEFAULT_CLOUD_PORT):
                vol.All(int, vol.Range(min=1, max=65535)),
            vol.Required(CONF_LISTEN_PORT, default=DEFAULT_LISTEN_PORT):
                vol.All(int, vol.Range(min=1, max=65535)),
            vol.Required(CONF_ON_VALUE, default=DEFAULT_ON_VALUE):
                vol.In({1: "1", 2: "2"}),
        })
        return self.async_show_form(step_id="user", data_schema=schema,
                                    errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RshSocketOptionsFlow()


class RshSocketOptionsFlow(OptionsFlow):
    """Lets the user flip which value means 'on' without re-adding the device."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None
                              ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_ON_VALUE,
            self.config_entry.data.get(CONF_ON_VALUE, DEFAULT_ON_VALUE))
        schema = vol.Schema({
            vol.Required(CONF_ON_VALUE, default=current): vol.In({1: "1", 2: "2"}),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
