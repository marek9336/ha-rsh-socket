# RSH Smart Socket — Home Assistant control for a cloud-only Wi-Fi socket

[![hacs][hacs-badge]][hacs-url]

Some cheap Wi-Fi sockets — sold as *RSH Smart Home*, *Rising Sun Home* and
various unbranded clones, usually an ESP8266 inside — cannot be controlled
locally at all:

- they **listen on no port**, so a port scan finds nothing to talk to;
- they are **not Tuya**, so `tuya-convert`, LocalTuya and `tuya-local` do not
  apply;
- the only way to reflash them is a **serial port**, i.e. opening the case.

What they *do* is keep a single long-lived TCP connection to a vendor cloud.
This integration puts Home Assistant **in the middle of that connection**.

The socket keeps working exactly as before — including the vendor app and
Google Home — but Home Assistant can now inject its own commands, and it sees
commands sent by the cloud, so its state stays in sync when you switch the
socket elsewhere.

> Written for a socket hidden inside a greasy kitchen extractor hood, where
> "just take it apart and flash Tasmota" was not a realistic option.

## Table of contents

- [Is this your socket?](#is-this-your-socket)
- [How it works](#how-it-works)
- [Step 1: find the cloud address](#step-1-find-the-cloud-address)
- [Step 2: redirect the socket](#step-2-redirect-the-socket)
- [Step 3: install the integration](#step-3-install-the-integration)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Protocol](#protocol)

## Is this your socket?

Before anything else, check that your socket really is this kind of device.
Scan it (replace the address with your socket's):

```bash
nmap -Pn -p 1-65535 192.168.1.50
```

You are in the right place if **no port is open** — no `80`, no `6668`
(Tuya), no `48899`. If port `6668` *is* open, you have a Tuya device and
[LocalTuya](https://github.com/rospogrigio/localtuya) will serve you far
better than this.

Then confirm the device holds one outbound connection to a cloud, usually on a
non-standard port. See the next step for how to find it.

## How it works

```
        before                                after

  ┌────────┐                          ┌────────┐
  │ socket │                          │ socket │
  └───┬────┘                          └───┬────┘
      │ TCP, unencrypted                  │  (redirected by your router)
      ▼                                   ▼
  ┌────────┐                          ┌───────────────┐
  │ vendor │                          │ Home Assistant│──┐
  │ cloud  │                          └───────────────┘  │ forwards everything
  └────────┘                                             ▼
                                                    ┌────────┐
                                                    │ vendor │
                                                    │ cloud  │
                                                    └────────┘
```

Nothing is blocked and nothing is decrypted — the traffic is not encrypted in
the first place. Home Assistant simply relays it and can add commands of its
own.

## Step 1: find the cloud address

You need the **IP address and port** your socket talks to. Pick whichever
method fits your setup.

**From your router's connection tracking.** Look for a long-lived TCP session
whose source is the socket. On MikroTik:

```
/ip firewall connection print where src-address~"192.168.1.50"
```

On OpenWrt:

```sh
conntrack -L | grep 192.168.1.50
```

**From a packet capture**, if your router cannot list connections — mirror the
socket's traffic and look for the destination it keeps returning to.

You are looking for a connection that **stays established for hours** (the
sockets tested keep it for 24 h) with small packets every ~30 seconds. Write
down the destination address and port; on the devices tested the port was
**8788**.

> ⚠️ The address may differ by region and may change if the vendor moves
> servers. If the socket stops responding months later, check it again.

## Step 2: redirect the socket

The socket must reach Home Assistant instead of the cloud. There are two ways;
**try DNS first**, it is simpler and avoids the hairpin problem below.

### Option A — DNS rewrite (simplest, if it works)

If the socket resolves a hostname rather than using a hard-coded IP, just point
that hostname at Home Assistant. Works with Pi-hole, AdGuard Home, dnsmasq or
any router that can override a DNS record.

First find out what it asks for — watch DNS queries coming from the socket's
address, e.g. in Pi-hole's query log or:

```sh
tcpdump -n -i any port 53 and host 192.168.1.50
```

If you see a hostname, map it to Home Assistant's IP:

```
# AdGuard Home: Filters -> DNS rewrites
device.example-vendor.com  ->  192.168.1.10

# dnsmasq / OpenWrt (/etc/config/dhcp)
address=/device.example-vendor.com/192.168.1.10
```

Then power-cycle the socket so it resolves again.

**If you see no DNS query at all**, the address is hard-coded — use Option B.

### Option B — destination NAT on the router

Redirect the socket's traffic to Home Assistant. In the examples:

| placeholder | meaning |
|---|---|
| `192.168.1.50` | the socket |
| `192.168.1.10` | Home Assistant |
| `203.0.113.45` | the vendor cloud you found in step 1 |
| `8788` | the cloud port |

**MikroTik (RouterOS):**

```
/ip firewall nat add chain=dstnat protocol=tcp \
    src-address=192.168.1.50 dst-address=203.0.113.45 dst-port=8788 \
    action=dst-nat to-addresses=192.168.1.10 to-ports=8788 \
    comment="smart socket -> Home Assistant"
```

**OpenWrt (firewall rule):**

```sh
uci add firewall redirect
uci set firewall.@redirect[-1].name='smart socket -> Home Assistant'
uci set firewall.@redirect[-1].src='lan'
uci set firewall.@redirect[-1].proto='tcp'
uci set firewall.@redirect[-1].src_ip='192.168.1.50'
uci set firewall.@redirect[-1].src_dip='203.0.113.45'
uci set firewall.@redirect[-1].src_dport='8788'
uci set firewall.@redirect[-1].dest_ip='192.168.1.10'
uci set firewall.@redirect[-1].dest_port='8788'
uci commit firewall && /etc/init.d/firewall reload
```

**pfSense / OPNsense:** *Firewall → NAT → Port Forward*, interface LAN,
protocol TCP, source = the socket, destination = the cloud address, port =
`8788`, redirect target = Home Assistant, same port.

**Plain Linux router (nftables):**

```sh
nft add rule ip nat prerouting ip saddr 192.168.1.50 \
    ip daddr 203.0.113.45 tcp dport 8788 \
    dnat to 192.168.1.10:8788
```

> ⚠️ **The socket holds its connection for hours, so a new rule does nothing
> until the old session ends.** Either drop the tracked connection
> (`/ip firewall connection remove …` on MikroTik, `conntrack -D …` on Linux)
> or simply **power-cycle the socket**.

### Hairpin: when the socket and Home Assistant share a subnet

This one is easy to miss and looks exactly like a broken integration — the
connection tracker shows the socket stuck in `SYN_SENT` and the integration
reports `connection_attempts: 0`.

The socket sends its packet to the router, the router rewrites the destination
to Home Assistant and sends it **back into the same subnet**. Home Assistant
then answers the socket **directly, from its own address** — but the socket is
waiting for a reply from the cloud address, so it drops it.

Fix it by masquerading that one connection, so the reply travels back through
the router:

```
# MikroTik
/ip firewall nat add chain=srcnat protocol=tcp \
    src-address=192.168.1.50 dst-address=192.168.1.10 dst-port=8788 \
    action=masquerade comment="hairpin for smart socket"
```

```sh
# nftables
nft add rule ip nat postrouting ip saddr 192.168.1.50 \
    ip daddr 192.168.1.10 tcp dport 8788 masquerade
```

> ⚠️ **With masquerading the connection reaches Home Assistant from the
> router, not from the socket.** Leave **Socket IP address empty** in the
> integration, otherwise it rejects the connection as coming from a stranger.

DNS rewrite (Option A) does not have this problem at all.

### If the socket is on a separate VLAN or guest network

A NAT rule is not enough — the firewall must also allow that one connection
through to Home Assistant. Keep the exception as narrow as you can: one source
address, one destination address, one port. Do not open the whole VLAN.

## Step 3: install the integration

**HACS:** *HACS → Integrations → ⋮ → Custom repositories*, add this repository
with category **Integration**, install it, restart Home Assistant, then add it
under *Settings → Devices & Services → Add integration → RSH Smart Socket*.

**Manual:** copy `custom_components/rsh_socket` into your Home Assistant
`config` folder and restart.

### Configuration

| Field | Meaning |
|---|---|
| **Name** | Whatever you want the device called |
| **Socket IP address** | Optional. Rejects connections from anything else. **Leave empty when using hairpin masquerading.** |
| **Vendor cloud IP address** | From step 1 |
| **Vendor cloud port** | From step 1, often `8788` |
| **Port to listen on** | Usually the same as the cloud port |
| **Value that means ON** | `1` or `2` — see below |

### If the switch works inverted

The payload value that closes the relay differs between vendors and was
determined by observation, not documentation. If on and off are swapped, open
the integration's **options** and flip *Value that means ON*. No need to
remove and re-add anything.

## Troubleshooting

The switch entity exposes diagnostic attributes, because a Home Assistant
install may have no readable log file:

| attribute | meaning |
|---|---|
| `connected` | whether the socket is currently connected |
| `connection_attempts` | how many times anything connected |
| `last_peer` | the address it connected from |
| `last_error` | why the last attempt failed |
| `rssi` | signal strength reported by the socket |

**`connection_attempts` stays 0** — traffic is not reaching Home Assistant.
The redirect is not in place, the old connection is still alive (power-cycle
the socket), or a firewall is dropping it. If the connection tracker shows
`SYN_SENT`, read the [hairpin](#hairpin-when-the-socket-and-home-assistant-share-a-subnet)
section.

**`last_error: rejected …, expected …`** — the connection came from a
different address than configured. With masquerading this is the router.
Clear the *Socket IP address* field.

**`last_error: upstream … unreachable`** — Home Assistant cannot reach the
vendor cloud. Check that it is allowed out to that address and port.

**Entity is `unknown` after a restart** — expected. The socket never reports
its relay position, so the state is unknown until something switches it.

**Changed the code and nothing happened** — Home Assistant caches Python
modules. Restarting the integration is not enough, restart Home Assistant.

## Limitations

- **The relay state is optimistic.** The device's telemetry frame carries
  signal strength, not the relay position, so the true state cannot be read.
  It is corrected as soon as any command passes through, including ones you
  send from the vendor app.
- **Home Assistant becomes a single point of failure.** While the redirect is
  active and this integration is not running, the socket cannot reach its
  cloud and will not respond to the vendor app or Google Home either. Remove
  the redirect to restore direct operation.
- **The traffic still goes to the vendor cloud.** This is a relay, not a
  blocker. If you want to cut the cloud off entirely you would have to
  emulate it, which this integration does not attempt.
- Tested against one socket model. Others speaking the same protocol may work;
  the frame format is documented below and in `proxy.py`.

## Protocol

```
04 01 01 02 02 01 00 00              heartbeat, device -> cloud, every ~30 s
04 01 02 02 02 <seq> 00 00           heartbeat reply, cloud -> device
04 01 01 02 09 00 00 01 <rssi>       telemetry, signal strength
04 01 01 02 07 <seq> 00 02 <val> 00  command, cloud  -> device
04 01 02 02 07 <seq> 00 02 <val> 01  ack,     device -> cloud
```

- Byte 2 is the **role** (`01` request, `02` reply) — **not** the direction.
  Both sides send `01` when they initiate, which is easy to misread.
- Byte 4 is the message type, byte 5 a sequence counter that increments per
  command, byte 8 the value.
- Nothing is encrypted.

Reverse engineered by observing traffic while switching the socket from the
vendor app, then confirming that injected frames are acknowledged.

## Contributing

If your socket speaks this protocol but behaves differently, open an issue
with a capture of the frames — especially the command frames while you switch
it from the vendor app. Please redact your own addresses first.

## Disclaimer

This is unofficial and not affiliated with any vendor. It works by redirecting
traffic from a device on **your own network**, which you are responsible for.
It may stop working at any time if the vendor changes their protocol.

## License

MIT — see [LICENSE](LICENSE).

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
