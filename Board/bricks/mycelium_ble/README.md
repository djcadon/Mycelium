# Mycelium BLE Brick

This folder is the authoritative source for the Arduino UNO Q BLE runtime:

- `ble.py` starts the GATT service and exposes the `run`, `send`, `ping`, and
  `sos` commands.
- `peer.py` discovers neighboring UNO Q peers and relays queued frames.
- `protocol.py` defines the compact BLE wire format.

The Brick image copies these files into `/app` and starts the peer daemon with
`python3 -u ble.py run`. Keep the three Python modules together when building
or deploying the Brick. The desktop WebSocket bridge remains in `Backend/`.

Each board derives its `Node-XXXX` identity from the final four hexadecimal
digits of its BLE adapter MAC address unless `MYCELIUM_NODE_ID` is explicitly
set. Reserve `Node-0000` for the laptop BLE bridge and configure
`MYCELIUM_GATEWAY_NODE_ID=Node-0000` on every board. Check for suffix
collisions between boards. `Node-FFFF` remains the broadcast address.

The four hex digits are a human-readable representation of the 16-bit binary
origin/target fields; the address is not transmitted as four ASCII characters.
The current frame remains 20 bytes, includes a 32-bit message ID, and carries
up to 9 payload bytes.

When two direct peers advertise the same ID, the peer detecting the conflict
quarantines both BLE addresses and removes their routes and RSSI measurements.
If the local board's ID is duplicated, it stops originating packets and route
advertisements. Logs identify the collision; assign one board an unused
`MYCELIUM_NODE_ID` and restart the affected peer daemons to clear quarantine.

Set `MYCELIUM_LOG_LEVEL` to `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`
to control diagnostic logs; it defaults to `INFO`. `DEBUG` includes connection
attempts, operation phases, and tracebacks. Connection/write failures are
reported with the phase that failed.

Diagnostic logs go to stderr. Structured event dumps are `DEBUG` only, so
periodic node announcements do not clutter normal container output. The laptop
bridge receives topology and packet reports directly over the BLE mesh.

## Laptop BLE bridge

Run `Backend/bridge.py` on Windows with `--node-id Node-0000`. The laptop acts
as a BLE mesh peer: its GATT service receives board writes, and its BLE central
discovers/connects to UNO Q peers to relay queued frames. The WebSocket UI
connection remains loopback-only at `ws://localhost:8765`; no Wi-Fi or serial
backhaul is used.

The desktop UI queues a `CHAT` frame in the laptop's local mesh queue with a
four-hop TTL. Queue acceptance is reported separately from delivery. Only the
selected destination's matching BLE `ACK` produces `DELIVERY_ACK`. The current
BLE text payload is limited to 9 UTF-8 bytes.

SOS event reports retry up to three times and are acknowledged by the laptop
after BLE handoff. Other packet reports are best-effort. The `sos` command
prints the SOS message ID; clear that exact alert with
`python3 ble.py sos-clear <sos_message_id>`. A clear is accepted only for an
alert from the same origin node.

Direct BLE neighbor RSSI reports are encoded in 20-byte `LINK_REPORT` frames
and flooded with TTL and duplicate suppression. Reports are sent at most once
per peer every five seconds. Each observing peer keeps a rolling 30-second
window of its own direct RSSI samples and reports its median, median absolute
deviation (MAD), and sample count. The laptop receives these summaries over
the BLE mesh and displays them without recomputing them. These are
signal-quality statistics, not calibrated distance or physical position.

## Distributed routes

Peers exchange compact route advertisements directly over BLE every five
seconds. Advertisements have a 30-second lease, use hop count as routing cost,
and support up to 32 known destinations and four hops. Split-horizon poison
reverse prevents advertising a route back through its selected next hop.
Known unicast frames use the selected neighbor; unknown or expired routes use
the bounded flood fallback. The laptop peer publishes its own route table to
the desktop UI. The displayed hop count does not represent physical distance,
and route convergence and recovery must still be validated with multiple
boards.
