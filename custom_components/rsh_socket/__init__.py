"""RSH Smart Socket - local control for cloud-only sockets via a proxy."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_CLOUD_HOST,
    CONF_CLOUD_PORT,
    CONF_DEVICE_IP,
    CONF_LISTEN_PORT,
    DEFAULT_CLOUD_PORT,
    DEFAULT_LISTEN_PORT,
    DOMAIN,
)
from .proxy import SocketProxy

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start the proxy for one socket."""
    data = entry.data
    proxy = SocketProxy(
        listen_port=data.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT),
        cloud_host=data[CONF_CLOUD_HOST],
        cloud_port=data.get(CONF_CLOUD_PORT, DEFAULT_CLOUD_PORT),
        device_ip=data.get(CONF_DEVICE_IP),
    )

    try:
        await proxy.start()
    except OSError as err:
        # Most often the port is already taken by another instance.
        raise ConfigEntryNotReady(
            f"Cannot listen on port {data.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT)}: {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = proxy
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop the proxy and release the port."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    proxy: SocketProxy | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if proxy is not None:
        await proxy.stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
