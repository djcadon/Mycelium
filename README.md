# Mycelium

> **Autonomous Peer-to-Peer Mesh Network System**  
> *When the grid goes down, the network awakens.*

---

## Overview

**Mycelium** is a decentralized, router-free mesh communication network designed for off-grid resilience and disaster relief. Instead of relying on centralized internet service providers, cell towers, or pre-existing Wi-Fi routers, Mycelium allows individual devices to interconnect directly antenna-to-antenna as equal peers using **Bluetooth Low Energy (BLE 5.1)**.

Each device functions simultaneously as an endpoint and a dynamic store-and-forward router—collaborating with neighboring peers to discover network topology, forward packets hop-by-hop across physical space, and heal routes automatically when nodes disconnect.

---

## Key Features

- **Pure Peer-to-Peer (Zero Central Authority):** No master servers, gateways, or routers. Every node runs symmetric software as an equal peer.
- **Direct Off-Grid Radio:** Antenna-to-antenna communication using Bluetooth 5.1 with zero Wi-Fi dependencies.
- **Dynamic Multi-Hop Relay & Self-Healing:** Packets hop through intermediate peers to reach distant targets using compact 20-byte routing frames with TTL and deduplication.
- **Physical Hardware Feedback:** Real-time LED pulses and buzzers on Arduino Uno Q hardware indicate packet transmission, reception, and alert states.
- **Interactive 3D Spatial Visualizer:** A mission-control dashboard built with React Three Fiber, rendering real-time nodes, signal quality (RSSI), in-flight packet animations, and telemetry.

---

## Hardware Platform

- **Nodes:** Arduino Uno Q boards (and ESP32-C3 support)
  - **Linux Subsystem (Qualcomm QRB2210):** Runs the BLE mesh daemon, peer discovery, and store-and-forward routing.
  - **Real-Time Subsystem (STM32):** Interfaces with physical sensors, SOS emergency buttons, and hardware status LEDs.

---

## Repository Structure

```text
Mycelium/
├── README.md               # Project documentation
├── LICENSE                 # License
├── Board/
│   └── bricks/mycelium_ble/ # Authoritative UNO Q BLE runtime and wire protocol
├── Backend/                # Laptop BLE node and WebSocket bridge
│   ├── bridge.py           # Loopback WebSocket bridge for the laptop BLE node
│   └── localization.py     # RF localization utilities
└── Frontend/               # 3D interactive mesh visualizer & HUD
    ├── src/
    │   ├── components/     # 3D scene (MeshScene.tsx) & HUD (NetworkHUD.tsx)
    │   ├── types/          # P2P mesh data models (mesh.ts)
    │   ├── App.tsx         # Mesh state manager & WebSocket client
    │   └── main.tsx        # Entry point
    ├── public/             # Static assets & custom mushroom favicon
    └── package.json        # Dependencies (React, Three.js, R3F, Tailwind)
```

---

## Getting Started

### 1. Start the BLE Mesh Daemon on Uno Q

The Arduino UNO Q implementation is maintained in
`Board/bricks/mycelium_ble`. Build and run the `mycelium_ble` Brick on each
board, setting a unique `MYCELIUM_NODE_ID` for each one. The Arduino runtime
lives only in that Brick folder. The laptop runs the same mesh queue/peer
transport with a Windows BLE GATT service and serves the UI over loopback
WebSocket; it does not use a Wi-Fi or serial backhaul.

```bash
# For direct CLI testing from the Brick folder:
python3 ble.py run

# Queue a message from another shell on that board:
python3 ble.py send "Hello Mesh!"
# or trigger emergency SOS:
python3 ble.py sos
```

On each Linux peer, keep `ble.py run` open. Run `send`, `ping`, or `sos` from
another terminal under the same user. The command queues the frame through a
local Unix socket. The peer advertises and scans independently of the CLI,
and tries each discovered Mycelium peer with pending frames. No IP network or
central server is required.

The queue is in memory and bounded to 128 frames. Frames are 20 bytes, with a
32-bit message ID and up to 9 payload bytes; duplicate suppression uses the
origin and message ID. Route advertisements run every five seconds, expire
after 30 seconds, and support routes up to four BLE hops. Known unicast packets
use the selected next hop; unknown destinations retain bounded flooding as a
fallback. Set a unique `Node-XXXX` ID on every board. `--peer <Bluetooth
address>` bypasses the queue for a direct test, and `listen` remains available
for one-shot debugging.


### 2. Start the laptop BLE node and UI bridge

Reserve `Node-0000` for the laptop and give each UNO Q a unique ID. On Windows,
install the backend requirements and start the bridge:

```powershell
py -m pip install -r Backend\requirements.txt
py Backend\bridge.py --node-id Node-0000
```

The laptop advertises the Mycelium GATT service and scans/connects to UNO Q
peers over BLE. The browser connects to the loopback WebSocket at
`ws://localhost:8765`. Each peer maintains its own rolling RSSI summary and
distributes it over BLE; RSSI is signal-quality telemetry, not calibrated
range or physical position. Route hop count is routing cost, not distance.

### 3. Start the 3D Visualizer on Laptop
```bash
# Terminal: Start React Frontend
cd Frontend
npm run dev
```

Open `http://localhost:5173` in your browser to interact with the 3D topology canvas.
