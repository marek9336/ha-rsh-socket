"""Transparent proxy between a cloud-only smart socket and its vendor cloud.

The socket keeps a single long-lived TCP connection to its vendor cloud and
never listens on any port, so it cannot be controlled locally. This proxy sits
in the middle: it forwards everything both ways (so the device keeps working
in the vendor app and in Google Home) while also being able to inject its own
commands and to observe commands issued by the cloud.

Wire format (reverse engineered 2026-08-12):

    04 01 01 02 02 01 00 00              heartbeat, device -> cloud, ~30 s
    04 01 02 02 02 <seq> 00 00           heartbeat reply, cloud -> device
    04 01 01 02 09 00 00 01 <rssi>       telemetry (signal strength)
    04 01 01 02 07 <seq> 00 02 <val> 00  command,   cloud  -> device
    04 01 02 02 07 <seq> 00 02 <val> 01  ack,       device -> cloud

Byte 2 is the role (01 = request, 02 = reply) -- it is *not* the direction.
Byte 4 is the message type, byte 5 a sequence counter, byte 8 the value.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

MAGIC = 0x04
ROLE_REQUEST = 0x01
ROLE_REPLY = 0x02
TYPE_HEARTBEAT = 0x02
TYPE_COMMAND = 0x07
TYPE_TELEMETRY = 0x09


def build_command(seq: int, value: int) -> bytes:
    """Build a command frame the socket accepts."""
    return bytes([MAGIC, 0x01, ROLE_REQUEST, 0x02, TYPE_COMMAND,
                  seq & 0xFF, 0x00, 0x02, value & 0xFF, 0x00])


class SocketProxy:
    """Forwards traffic between the socket and its cloud, and injects commands."""

    def __init__(self, listen_port: int, cloud_host: str, cloud_port: int,
                 device_ip: str | None = None) -> None:
        self._listen_port = listen_port
        self._cloud_host = cloud_host
        self._cloud_port = cloud_port
        self._device_ip = device_ip

        self._server: asyncio.AbstractServer | None = None
        self._to_device: asyncio.StreamWriter | None = None
        self._pending: list[bytes] = []
        self._seq = 0

        self.connected = False
        self.rssi: int | None = None
        self.last_value: int | None = None
        # Diagnostics -- surfaced as entity attributes, because a Home Assistant
        # install may have no log file to read.
        self.attempts = 0
        self.last_peer: str | None = None
        self.last_error: str | None = None
        self._listeners: list[Callable[[], None]] = []

    # ---------------------------------------------------------------- helpers

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired whenever observed state changes."""
        self._listeners.append(cb)

        def _remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return _remove

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001 - a bad listener must not kill the proxy
                _LOGGER.exception("State listener failed")

    # ----------------------------------------------------------- lifecycle

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_device, "0.0.0.0", self._listen_port)
        _LOGGER.info("Proxy listening on port %s, upstream %s:%s",
                     self._listen_port, self._cloud_host, self._cloud_port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        self.connected = False
        self._notify()

    # ------------------------------------------------------------- commands

    async def send_command(self, value: int) -> bool:
        """Queue a command frame. Returns False when the socket is not connected."""
        if not self.connected:
            _LOGGER.warning("Cannot send command, socket is not connected")
            return False
        self._seq = (self._seq + 1) & 0xFF
        self._pending.append(build_command(self._seq, value))
        return True

    # ------------------------------------------------------------- internals

    def _observe(self, data: bytes, from_device: bool) -> None:
        """Update state from an observed frame. Never raises."""
        if len(data) < 5 or data[0] != MAGIC:
            return
        msg_type = data[4]
        if msg_type == TYPE_TELEMETRY and len(data) >= 9:
            # Value tracks signal strength, not the relay; it drifts on its own.
            self.rssi = -data[-1]
            self._notify()
        elif msg_type == TYPE_COMMAND and len(data) >= 9:
            # A command from the cloud means somebody used the vendor app or
            # Google Home -- mirror it so Home Assistant stays in sync.
            if not from_device:
                self.last_value = data[8]
                self._seq = max(self._seq, data[5])
                self._notify()

    async def _pump(self, reader: asyncio.StreamReader,
                    writer: asyncio.StreamWriter, from_device: bool) -> None:
        try:
            while True:
                if not from_device and self._pending:
                    frame = self._pending.pop(0)
                    writer.write(frame)
                    await writer.drain()
                    _LOGGER.debug("Injected %s", frame.hex(" "))
                try:
                    data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    break
                self._observe(data, from_device)
                writer.write(data)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Forwarding stopped unexpectedly")
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _handle_device(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else None
        self.attempts += 1
        self.last_peer = peer_ip
        if self._device_ip and peer_ip != self._device_ip:
            self.last_error = f"rejected {peer_ip}, expected {self._device_ip}"
            _LOGGER.warning("Rejecting connection from %s (expected %s)",
                            peer_ip, self._device_ip)
            self._notify()
            writer.close()
            return

        _LOGGER.info("Socket connected from %s", peer_ip)
        try:
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(self._cloud_host, self._cloud_port),
                timeout=15)
        except Exception as err:  # noqa: BLE001
            # Closing lets the socket retry; better than holding a dead session.
            self.last_error = f"upstream {self._cloud_host}:{self._cloud_port} - {err!r}"
            _LOGGER.error("Upstream cloud unreachable (%s), dropping session", err)
            self._notify()
            writer.close()
            return
        self.last_error = None

        self._to_device = writer
        self.connected = True
        self._notify()
        try:
            await asyncio.gather(
                self._pump(reader, cloud_writer, True),
                self._pump(cloud_reader, writer, False))
        finally:
            self.connected = False
            self._to_device = None
            self._pending.clear()
            self._notify()
            _LOGGER.info("Socket disconnected")
