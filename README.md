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
├── Backend/                # Python BLE Mesh & 3D Spatial Engine
│   ├── ble.py              # BLE mesh link & multi-hop store-and-forward daemon
│   ├── protocol.py         # Compact 20-byte packet encoder/decoder
│   ├── localization.py     # 3D RF path-loss & spring-relaxation physics solver
│   └── bridge.py           # USB serial to WebSocket bridge (ws://localhost:8765)
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
```bash
# On Board 1 (Listener / Base):
python3 ble.py listen

# On Board 2 (Broadcaster / Field Node):
python3 ble.py send "Hello Mesh!"
# or trigger emergency SOS:
python3 ble.py sos
```

### 2. Start the 3D Visualizer on Laptop
```bash
# Terminal 1: Start 3D WebSocket bridge
cd Backend
python bridge.py

# Terminal 2: Start React Frontend
cd Frontend
npm run dev
```

Open `http://localhost:5173` in your browser to interact with the 3D topology canvas.
