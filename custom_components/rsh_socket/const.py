"""Constants for the RSH Smart Socket integration."""

DOMAIN = "rsh_socket"

CONF_DEVICE_IP = "device_ip"
CONF_CLOUD_HOST = "cloud_host"
CONF_CLOUD_PORT = "cloud_port"
CONF_LISTEN_PORT = "listen_port"
CONF_ON_VALUE = "on_value"

DEFAULT_CLOUD_PORT = 8788
DEFAULT_LISTEN_PORT = 8788
# Which payload value switches the relay on. Verified against real hardware:
# 1 closes the relay, 2 opens it. Vendors may differ, so it stays configurable
# -- flip it in the options if the switch works inverted.
DEFAULT_ON_VALUE = 1
