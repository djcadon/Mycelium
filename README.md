# Mycelium 🍄📡

> **Autonomous Peer-to-Peer Mesh Network System**  
> *When the grid goes down, the network awakens.*

---

## Overview

**Mycelium** is a decentralized, router-free mesh communication network designed for off-grid resilience and disaster relief. Instead of relying on centralized internet service providers, cell towers, or pre-existing Wi-Fi routers, Mycelium allows individual devices to interconnect directly antenna-to-antenna as equal peers. 

Each device functions simultaneously as an endpoint and a dynamic router—collaborating with neighboring peers to discover network topology, forward packets hop-by-hop across physical space, and heal routes automatically when nodes disconnect.

---

## Key Features

- **Pure Peer-to-Peer (Zero Central Authority):** No master servers, gateways, or routers. Every node runs symmetric software and operates as an equal peer.
- **Direct Off-Grid Radio:** Connects hardware boards antenna-to-antenna using direct ad-hoc / IBSS wireless channels.
- **Dynamic Multi-Hop Relay & Self-Healing:** Packets hop through intermediate peers to reach distant targets. If a relay drops out or moves out of range, the mesh dynamically reroutes around the failure.
- **Physical Hardware Feedback:** Real-time LED pulses and buzzers on Arduino Uno Q hardware indicate packet transmission, reception, and alert states.
- **Interactive 3D Spatial Visualizer:** A mission-control dashboard built with React Three Fiber, rendering real-time nodes, signal quality (RSSI), in-flight packet animations, and telemetry.

---

## Hardware Platform

- **Nodes:** Arduino Uno Q boards
  - **Linux Subsystem (Qualcomm QRB2210):** Handles ad-hoc wireless networking, peer discovery, and dynamic routing logic.
  - **Real-Time Subsystem (STM32):** Interfaces with physical sensors, SOS emergency buttons, and hardware status LEDs.

---

## Getting Started (Frontend Visualizer)

### Prerequisites
- Node.js (v18+)
- npm

### Installation & Run
```bash
# Navigate to the frontend directory
cd Frontend

# Install dependencies
npm install

# Start the local development server
npm run dev
```

Open `http://localhost:5173` in your browser to interact with the 3D topology canvas.
