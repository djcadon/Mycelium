# Mycelium three-node BLE mesh test

Use the same updated App Lab app on all three UNO Q boards. Stop old Mycelium
instances first. In bricks/mycelium_ble/brick_compose.yaml, set a unique
MYCELIUM_NODE_ID per board (for example Node-8101, Node-6EBB, Node-0003).
Set the /matrix volume source to that board's actual App Lab app python folder.
Rebuild the Brick and run the app on each board.

The UNO Q BLE runtime source of truth is
`Board/bricks/mycelium_ble/{ble.py,peer.py,protocol.py}`. The Dockerfile copies
those modules into the Brick image. The Arduino source is not duplicated under
`Backend/`; that directory contains the desktop bridge and Wi-Fi experiment.

The peers advertise the same mesh service and automatically discover neighbors.
Each peer opens and keeps its outbound GATT links. Both directions use GATT
writes on outbound sessions. No peer address needs to be configured.
The peer retains queued frames in memory for up to 120 seconds and relays
them to eligible neighbors, excluding the immediate sender when BlueZ
provides its address. Receivers deduplicate by origin and message ID.

From the sending board's active app directory:

```sh
docker compose -f .cache/app-compose.yaml exec -T mycelium_peer \
  python3 /app/ble.py send Node-0003 HELLO
```

Put Node-6EBB between Node-8101 and Node-0003, with the end nodes outside
direct BLE reach. Check for Hop Count: 1 at Node-0003. Repeating the same
text generates a fresh message ID. The LED matrix displays received chat.
The new 20-byte frame carries at most 9 UTF-8 bytes. Use the same version
on all participating Q boards. This release targets Linux UNO Q peers.
