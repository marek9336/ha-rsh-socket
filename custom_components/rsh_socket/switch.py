"""Switch entity backed by the proxy."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_IP, CONF_ON_VALUE, DEFAULT_ON_VALUE, DOMAIN
from .proxy import SocketProxy


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    proxy: SocketProxy = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RshSocketSwitch(entry, proxy)])


class RshSocketSwitch(SwitchEntity):
    """The socket's relay.

    The device never reports its relay state -- the telemetry frame carries
    signal strength, not the relay -- so the entity is optimistic. It does
    however observe commands coming from the vendor cloud, which keeps it in
    sync when the socket is switched from the vendor app or Google Home.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_assumed_state = True

    def __init__(self, entry: ConfigEntry, proxy: SocketProxy) -> None:
        self._entry = entry
        self._proxy = proxy
        self._on_value: int = entry.options.get(
            CONF_ON_VALUE, entry.data.get(CONF_ON_VALUE, DEFAULT_ON_VALUE))
        self._off_value = 1 if self._on_value == 2 else 2
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="RSH / generic cloud socket",
            model="Wi-Fi smart socket (proxied)",
            configuration_url=None,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._proxy.add_listener(self._handle_proxy_update))

    @callback
    def _handle_proxy_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        if self._proxy.last_value is None:
            return None
        return self._proxy.last_value == self._on_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # The entity stays available while the proxy runs; whether the socket
        # itself is talking to us is reported here, together with why not.
        return {
            "connected": self._proxy.connected,
            "rssi": self._proxy.rssi,
            "device_ip": self._entry.data.get(CONF_DEVICE_IP),
            "connection_attempts": self._proxy.attempts,
            "last_peer": self._proxy.last_peer,
            "last_error": self._proxy.last_error,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        if await self._proxy.send_command(self._on_value):
            self._proxy.last_value = self._on_value
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if await self._proxy.send_command(self._off_value):
            self._proxy.last_value = self._off_value
            self.async_write_ha_state()
