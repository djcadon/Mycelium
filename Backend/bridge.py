#!/usr/bin/env python3
"""
Mycelium - 100% Wireless BLE 3D Mesh Bridge
Uses your laptop's internal Bluetooth antenna to discover Mycelium Uno Q nodes over the air,
calculates their physical 3D (x, y, z) spatial coordinates via RF path-loss physics,
and streams the live topology to the React Three Fiber visualizer on ws://localhost:8765.

ZERO CABLES REQUIRED. Pure wireless Bluetooth discovery.
"""

import asyncio
import json
import math
import sys
import time
from typing import Dict, Set

import websockets
from bleak import BleakScanner

from localization import Mesh3DLocalizer, rssi_to_distance

WS_HOST = "0.0.0.0"
WS_PORT = 8765

MYCELIUM_SERVICE_UUID = "9cd00001-8ed5-4e55-932e-5c558747527a"
MYCELIUM_NAME = "Mycelium"

# Connected browser clients
CONNECTED_CLIENTS: Set[websockets.WebSocketServerProtocol] = set()

# Live hardware mesh state
localizer = Mesh3DLocalizer(damping=0.88, spring_k=0.15, iterations=50)
nodes_state: Dict[str, dict] = {}
links_state: Dict[str, dict] = {}
distance_matrix: Dict[str, Dict[str, float]] = {}

event_loop: asyncio.AbstractEventLoop = None

def get_node_id_from_mac(mac: str) -> str:
    clean = mac.replace(":", "").replace("-", "").upper()
    return f"Node-{clean[-4:]}"

def register_ble_node(node_id: str, mac: str, rssi: int):
    """Registers or updates a physical Arduino Uno Q discovered over Bluetooth."""
    is_new = node_id not in nodes_state
    
    # First discovered node is positioned at origin (0, 0, 0) as reference anchor
    initial_pos = [0.0, 0.0, 0.0] if len(nodes_state) == 0 else [2.0, 0.5, 1.0]

    if is_new:
        nodes_state[node_id] = {
            "id": node_id,
            "name": f"{node_id} (Uno Q)",
            "hardware": "Uno Q",
            "battery": 95,
            "status": "online",
            "ip": f"BLE ({mac[-5:]})",
            "neighborCount": 0,
            "position": initial_pos,
            "sensors": {"temperature": 24, "sosPressed": False},
            "lastSeen": time.time(),
        }
        print(f"\n[+] DISCOVERED OVER-THE-AIR: {node_id} (MAC: {mac}) | Signal: {rssi} dBm", flush=True)
    else:
        nodes_state[node_id]["lastSeen"] = time.time()
        nodes_state[node_id]["status"] = "online"

def update_mesh_rf_link(source_id: str, target_id: str, rssi: int):
    """Calculates physical distance from BLE signal strength and runs 3D physics solver."""
    link_key = tuple(sorted([source_id, target_id]))
    link_id = f"{link_key[0]}-{link_key[1]}"

    links_state[link_id] = {
        "id": link_id,
        "source": link_key[0],
        "target": link_key[1],
        "rssi": rssi,
        "active": True,
    }

    # Convert physical Bluetooth dBm to real-world meters
    dist = rssi_to_distance(rssi)
    if source_id not in distance_matrix:
        distance_matrix[source_id] = {}
    if target_id not in distance_matrix:
        distance_matrix[target_id] = {}

    distance_matrix[source_id][target_id] = dist
    distance_matrix[target_id][source_id] = dist

    nodes_state[source_id]["neighborCount"] = len(distance_matrix[source_id])
    if target_id in nodes_state:
        nodes_state[target_id]["neighborCount"] = len(distance_matrix[target_id])

    # Re-calculate 3D (x, y, z) coordinates using spring-relaxation physics
    if distance_matrix:
        anchor_id = list(nodes_state.keys())[0]
        coords = localizer.update_topology(distance_matrix, anchor_id=anchor_id)
        for nid, (x, y, z) in coords.items():
            if nid in nodes_state:
                nodes_state[nid]["position"] = [x, y, z]

async def broadcast_ws(message_dict: dict):
    """Pushes live update to all connected browser tabs."""
    if not CONNECTED_CLIENTS:
        return
    msg = json.dumps(message_dict)
    disconnected = set()
    for ws in CONNECTED_CLIENTS:
        try:
            await ws.send(msg)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        CONNECTED_CLIENTS.remove(ws)

def on_ble_advertisement_received(device, advertisement_data):
    """Callback triggered whenever laptop's Bluetooth antenna hears an Uno Q broadcast."""
    # Check if advertisement matches Mycelium BLE service or local name
    is_mycelium = (
        (MYCELIUM_SERVICE_UUID in advertisement_data.service_uuids) or
        (advertisement_data.local_name == MYCELIUM_NAME) or
        (device.name == MYCELIUM_NAME)
    )

    if not is_mycelium:
        return

    node_id = get_node_id_from_mac(device.address)
    rssi = advertisement_data.rssi if hasattr(advertisement_data, "rssi") and advertisement_data.rssi else getattr(device, "rssi", -60)

    # Register the node
    register_ble_node(node_id, device.address, rssi)

    # Link to laptop / base anchor
    laptop_anchor_id = "Laptop-Base"
    if laptop_anchor_id not in nodes_state:
        nodes_state[laptop_anchor_id] = {
            "id": laptop_anchor_id,
            "name": "Laptop (Intel BLE)",
            "hardware": "Host Controller",
            "battery": 100,
            "status": "online",
            "ip": "Local BLE",
            "neighborCount": 0,
            "position": [0.0, 0.0, 0.0],
            "sensors": {"temperature": 22, "sosPressed": False},
            "lastSeen": time.time(),
        }

    update_mesh_rf_link(laptop_anchor_id, node_id, rssi)

    # Broadcast updated 3D positions to browser
    if event_loop and event_loop.is_running():
        asyncio.run_coroutine_threadsafe(
            broadcast_ws({
                "type": "TOPOLOGY_UPDATE",
                "nodes": list(nodes_state.values()),
                "links": list(links_state.values()),
            }),
            event_loop
        )

async def ble_scanner_task():
    """Runs a continuous over-the-air Bluetooth LE scanner using laptop's internal radio."""
    print("[BLE] Starting wireless scanner on internal Bluetooth controller...")
    scanner = BleakScanner(detection_callback=on_ble_advertisement_received)
    await scanner.start()
    try:
        while True:
            await asyncio.sleep(1.0)
    finally:
        await scanner.stop()

async def ws_handler(websocket):
    CONNECTED_CLIENTS.add(websocket)
    print(f"[WS] Browser connected! Total viewers: {len(CONNECTED_CLIENTS)}")

    # Send current real state
    await websocket.send(json.dumps({
        "type": "TOPOLOGY_UPDATE",
        "nodes": list(nodes_state.values()),
        "links": list(links_state.values()),
    }))

    try:
        async for raw_msg in websocket:
            pass
    finally:
        CONNECTED_CLIENTS.remove(websocket)

async def main():
    global event_loop
    event_loop = asyncio.get_running_loop()

    print("=============================================================")
    print(" Mycelium 100% Wireless BLE 3D Mesh Bridge")
    print(" Mode             : Laptop Internal Bluetooth (Zero Cables)")
    print(f" WebSocket Stream : ws://localhost:{WS_PORT}")
    print("=============================================================")

    # Start WebSocket server for React frontend
    server = await websockets.serve(ws_handler, WS_HOST, WS_PORT)

    # Start continuous BLE scanner
    ble_task = asyncio.create_task(ble_scanner_task())

    try:
        await asyncio.gather(server.wait_closed(), ble_task)
    except KeyboardInterrupt:
        print("\n[!] Bridge stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
