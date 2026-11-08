# Wi-Fi bridge experiment

This directory is isolated from the BLE implementation; nothing here changes
`Backend/ble.py`, `peer.py`, or `protocol.py`.

## Current: Local UI bridge over Guest Wi-Fi

The Wi-Fi bridge runs on the laptop, listens for node UDP traffic on the
laptop's Wi-Fi IPv4 address, and exposes the existing UI contract only on
`ws://127.0.0.1:8765`. Each UNO Q sends periodic presence messages to the
laptop and receives direct messages through the bridge. This is a hub-and-spoke
prototype, not a peer-routed or multi-hop mesh. IP links have no measured RSSI,
and node positions remain diagrammatic until a real positioning measurement is
available.

On Windows, install the bridge's WebSocket dependency if needed, find the
laptop's current Wi-Fi IPv4 address with `ipconfig`, and start the bridge from
the repository root:

```powershell
py -m pip install websockets
py Backend\WiFi\bridge.py --udp-bind <laptop-wifi-ip>
```

In another terminal, start the UI:

```powershell
Set-Location Frontend
npm run dev
```

Copy `node.py` and `wifi_protocol.py` together to each UNO Q. On both boards,
check the current address with `ip -brief addr`, then run the node agent using
the laptop's current Wi-Fi address and a unique stable ID:

```bash
python3 node.py --node-id Chudlet --name Chudlet --bridge-host <laptop-wifi-ip>
```

Use a different ID/name on the second board, for example `CamenUnoQ`. Keep the
bridge and node processes running while using the UI. The host may prompt to
allow inbound UDP on port `45802`; allow it only on the network profile being
used for this test. If peers do not appear, check the laptop firewall and
whether Guest Wi-Fi permits client-to-client traffic. Stop processes with
Ctrl+C. No Wi-Fi credentials are stored or sent by these scripts.

The UI reports a peer online after its heartbeat reaches the bridge and
reports a message delivered only after a response from the destination node.
The WebSocket remains loopback-only; do not change it to a LAN-facing bind.
This phase is distinct from the board-to-board UDP echo test below.

## Verified baseline: UDP echo over the existing Wi-Fi

Use the Guest Wi-Fi temporarily to verify that two boards can exchange IP
traffic. This tests UDP reachability and an application-level echo only; it is
not a decentralized or multi-hop mesh test. The script uses Python's standard
library, binds to one specified interface address, and echoes only a specified
peer address.

On the listener board, get its current Wi-Fi address with `ip -brief addr`, then
run (replace the example addresses if DHCP assigned different ones):

```bash
python3 udp_echo.py listen --bind 172.20.0.66 --allow-peer 172.20.0.53
```

On the other board:

```bash
python3 udp_echo.py send 172.20.0.66 --message "hello from Camen"
```

Swap the listener and sender to test the reverse direction. Stop the listener
with Ctrl+C. A timeout may mean that Guest Wi-Fi blocks communication between
clients, rather than a problem with the UDP code.

**Observed:** The two UNO Q boards exchanged a UDP message and echo over Guest
Wi-Fi. The sender's DHCP address had changed, so the listener allowlist needed
the current address. This validates IP transport between the boards on this
network, not AP-free peer-to-peer or multi-hop forwarding.

## Deferred: AP-free peer-to-peer link

The UNO Q driver reports Wi-Fi Direct/P2P device, client, and group-owner
modes, and the boards have discovered each other. Wi-Fi Direct forms a separate
group but assigns one peer a temporary group-owner role; it is not a symmetric
802.11s mesh. The driver output did not list `mesh point` under Supported
interface modes, so 802.11s is not confirmed. IBSS was tried previously and
should not be treated as the current path.

P2P group formation is not yet verified: the first connection request was
accepted, but the client interface stayed down and the peer did not show a
group interface. The Guest Wi-Fi UDP echo phase has succeeded. To retry P2P
without concurrent infrastructure Wi-Fi, first finish downloads and then
disconnect `wlan0` from Guest on both boards:

```bash
sudo nmcli device disconnect wlan0
```

Using the USB-C console, start a timed discovery on both boards:

```bash
sudo /usr/sbin/wpa_cli -i wlan0 P2P_FIND 60
```

On Chudlet, confirm CamenUnoQ appears in `P2P_PEERS`, then initiate one
non-persistent group negotiation using that peer address:

```bash
sudo /usr/sbin/wpa_cli -i wlan0 P2P_PEERS
sudo /usr/sbin/wpa_cli -i wlan0 P2P_CONNECT <peer-address> pbc
```

Do not run `P2P_CONNECT` on both boards. Check `sudo iw dev` and
`ip -brief addr` on both boards to verify that the group interface is up and
that IP connectivity exists. If the retry fails, use the diagnostic script:

```bash
./check_peer_modes.sh
```

The diagnostic is read-only. It reports driver interface modes and
combinations, active wireless interfaces, NetworkManager devices, links, IP
addresses, and radio block state. A reported mode alone does not prove that
group formation, IP configuration, or multi-hop forwarding works.
