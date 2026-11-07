"""
Mycelium - BLE Mesh Link & Multi-Hop Relay
Supports Uno Q (Linux / CPython) and ESP32-C3 (MicroPython).
Integrates 20-byte compact protocol, store-and-forward relay, and 3D telemetry.
"""

import sys
import json
import time
import os

try:
    from protocol import (
        pack_packet,
        unpack_packet,
        node_id_to_int,
        int_to_node_id,
        TYPE_HEARTBEAT,
        TYPE_SOS,
        TYPE_TELEMETRY,
        TYPE_CHAT,
        TOTAL_SIZE,
    )
except ImportError:
    # Minimal fallback for MicroPython
    TOTAL_SIZE = 20
    TYPE_HEARTBEAT = 0x01
    TYPE_SOS = 0x02
    TYPE_CHAT = 0x04

NAME = "Mycelium"
SERVICE = "9cd00001-8ed5-4e55-932e-5c558747527a"
INBOX = "9cd00002-8ed5-4e55-932e-5c558747527a"
MAX_BYTES = 20

def get_local_node_id() -> str:
    """Derives a stable Node ID from hardware MAC address."""
    for iface in ("wlan0", "eth0"):
        try:
            with open(f"/sys/class/net/{iface}/address", "r") as f:
                mac = f.read().strip().replace(":", "").upper()
                return f"Node-{mac[-4:]}"
        except Exception:
            pass
    try:
        import uuid
        mac = hex(uuid.getnode()).upper()
        return f"Node-{mac[-4:]}"
    except Exception:
        return "Node-0001"

MY_NODE_ID = get_local_node_id()

# Set of recently seen packet signatures to prevent routing loops
SEEN_PACKETS = set()

def emit_event(event_dict: dict):
    """Emits structured JSON over stdout for the 3D bridge on USB (COM4)."""
    try:
        print(json.dumps(event_dict), flush=True)
    except Exception:
        pass

def process_received_frame(raw_data: bytes, forward_callback=None):
    """Decodes a 20-byte BLE mesh frame and decides whether to consume or relay it."""
    data_bytes = bytes(raw_data)

    # 1. Try unpacking as a Mycelium 20-byte mesh protocol frame
    if len(data_bytes) == TOTAL_SIZE:
        try:
            pkt = unpack_packet(data_bytes)
            origin = pkt["origin"]
            target = pkt["target"]
            ttl = pkt["ttl"]
            hops = pkt["hop_count"]
            p_type = pkt["type"]
            msg = pkt["payload"]

            # Deduplication key
            sig = f"{origin}-{target}-{msg}"
            if sig in SEEN_PACKETS:
                return
            SEEN_PACKETS.add(sig)
            if len(SEEN_PACKETS) > 500:
                SEEN_PACKETS.pop()

            # Ignore packets originated by this node
            if origin == MY_NODE_ID:
                return

            print(f"\n[BLE MESH INBOX]")
            print(f"  Origin   : {origin}")
            print(f"  Target   : {target}")
            print(f"  Hop Count: {hops} (TTL: {ttl})")
            print(f"  Type     : {p_type}")
            print(f"  Message  : {msg}")

            # Pipe to 3D visualizer
            emit_event({
                "event": "PACKET_RX",
                "origin": origin,
                "target": target,
                "hops": hops,
                "type": p_type,
                "message": msg,
                "rssi": -55,
            })

            # Check if this node is the intended destination
            if target == MY_NODE_ID or target == "*":
                print(f"[✓] DESTINATION REACHED! Packet consumed by {MY_NODE_ID}")
                if p_type == "SOS":
                    print("[!!!] EMERGENCY SOS ALERT ACTIVE [!!!]", flush=True)
            elif target != MY_NODE_ID and ttl > 1:
                # We are an intermediate relay node: Forward packet!
                print(f"[➔] RELAYING HOP: Forwarding packet to {target} (Next TTL: {ttl - 1})")
                new_frame = pack_packet(
                    origin=origin,
                    target=target,
                    ttl=ttl - 1,
                    hop_count=hops + 1,
                    msg_type=pkt["type_id"],
                    payload=msg,
                )
                if forward_callback:
                    forward_callback(new_frame, target)

            return
        except Exception as e:
            pass

    # Fallback to plain UTF-8 string
    try:
        print("[BLE Raw Received]:", data_bytes.decode("utf-8"))
    except UnicodeError:
        print("[BLE Raw Received Invalid UTF-8]:", data_bytes)


# ==============================================================================
# Linux / CPython Implementation (BlueZ + dbus-fast + Bleak)
# ==============================================================================

async def linux_main(payload, peer, forward_mode=False):
    import asyncio

    if payload is not None:
        from bleak import BleakClient, BleakScanner

        def matches(device, advertisement):
            if peer and device.address.lower() != peer.lower():
                return False
            is_match = SERVICE in advertisement.service_uuids or advertisement.local_name == NAME
            if is_match and hasattr(device, "rssi") and device.rssi:
                # Stream RSSI of discovered peer to the 3D bridge
                clean_mac = device.address.replace(":", "").upper()
                peer_node_id = f"Node-{clean_mac[-4:]}"
                emit_event({
                    "event": "LINK_UPDATE",
                    "source": MY_NODE_ID,
                    "target": peer_node_id,
                    "rssi": device.rssi,
                })
            return is_match

        print(f"[{MY_NODE_ID}] Scanning for Mycelium BLE peers...", flush=True)
        device = await BleakScanner.find_device_by_filter(matches, timeout=8.0)
        if device is None:
            raise RuntimeError("No Mycelium BLE peer found in radio range")

        print(f"[{MY_NODE_ID}] Connecting to peer {device.address} (RSSI: {getattr(device, 'rssi', 'N/A')} dBm)...")
        async with BleakClient(device) as client:
            await client.write_gatt_char(INBOX, payload, response=True)
            print(f"[✓] BLE Mesh frame delivered to {device.address}")
        return

    await linux_listen()


async def linux_listen():
    import asyncio
    from dbus_fast import BusType, DBusError, PropertyAccess
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, dbus_property, method

    root = "/org/mycelium"
    service_path = root + "/service0"
    advertisement_path = "/org/mycelium_advertisement"
    gatt_manager = "org.bluez.GattManager1"
    advertising_manager = "org.bluez.LEAdvertisingManager1"

    loop = asyncio.get_running_loop()

    def do_relay_forward(frame_bytes, target_node):
        # Spawns a background task to relay the frame to the next hop
        asyncio.create_task(linux_main(frame_bytes, peer=None, forward_mode=True))

    class InboxService(ServiceInterface):
        def __init__(self):
            super().__init__("org.bluez.GattService1")

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return SERVICE

        @dbus_property(access=PropertyAccess.READ)
        def Primary(self) -> "b":
            return True

    class InboxCharacteristic(ServiceInterface):
        def __init__(self):
            super().__init__("org.bluez.GattCharacteristic1")

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return INBOX

        @dbus_property(access=PropertyAccess.READ)
        def Service(self) -> "o":
            return service_path

        @dbus_property(access=PropertyAccess.READ)
        def Flags(self) -> "as":
            return ["write"]

        @method()
        def WriteValue(self, value: "ay", options: "a{sv}"):
            offset = options.get("offset")
            if offset is not None and offset.value:
                raise DBusError("org.bluez.Error.InvalidOffset", "Expected offset zero")
            if not 1 <= len(value) <= MAX_BYTES:
                raise DBusError("org.bluez.Error.InvalidValueLength", "Expected 1-20 bytes")
            process_received_frame(value, forward_callback=do_relay_forward)

    class Advertisement(ServiceInterface):
        def __init__(self):
            super().__init__("org.bluez.LEAdvertisement1")

        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> "s":
            return "peripheral"

        @dbus_property(access=PropertyAccess.READ)
        def ServiceUUIDs(self) -> "as":
            return [SERVICE]

        @method()
        def Release(self):
            print("Mycelium BLE advertisement released")

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        bluez = "org.bluez"
        manager = bus.get_proxy_object(bluez, "/", await bus.introspect(bluez, "/"))
        objects = await manager.get_interface(
            "org.freedesktop.DBus.ObjectManager"
        ).call_get_managed_objects()
        adapter_path = next(
            (path for path, interfaces in objects.items()
             if gatt_manager in interfaces and advertising_manager in interfaces
             and interfaces["org.bluez.Adapter1"]["Powered"].value),
            None,
        )
        if adapter_path is None:
            raise RuntimeError("No powered BlueZ adapter with BLE advertising and GATT")

        adapter = bus.get_proxy_object(
            bluez, adapter_path, await bus.introspect(bluez, adapter_path)
        )
        bus.export(service_path, InboxService())
        bus.export(service_path + "/char0", InboxCharacteristic())
        bus.export(advertisement_path, Advertisement())
        await adapter.get_interface(gatt_manager).call_register_application(root, {})
        await adapter.get_interface(advertising_manager).call_register_advertisement(
            advertisement_path, {}
        )

        # Announce this node to the 3D bridge
        emit_event({"event": "NODE_ANNOUNCE", "node_id": MY_NODE_ID, "mac": ""})

        print("=============================================================")
        print(f" Mycelium BLE Mesh Node Active: {MY_NODE_ID}")
        print(f" GATT Service: {SERVICE}")
        print(f" INBOX UUID  : {INBOX} (Max: {MAX_BYTES}B)")
        print(" Listening for incoming peer packets and relay hops...")
        print("=============================================================", flush=True)

        await bus.wait_for_disconnect()
    finally:
        bus.disconnect()


# ==============================================================================
# MicroPython Implementation (ESP32-C3)
# ==============================================================================

def esp_main(payload, peer):
    import bluetooth
    import time

    CENTRAL_DISCONNECT = 2
    GATTS_WRITE = 3
    SCAN_RESULT, SCAN_DONE = 5, 6
    PERIPHERAL_CONNECT, PERIPHERAL_DISCONNECT = 7, 8
    CHARACTERISTIC_RESULT, CHARACTERISTIC_DONE = 11, 12
    WRITE_DONE = 17

    radio = bluetooth.BLE()
    radio.active(True)
    service_uuid = bluetooth.UUID(SERVICE)
    inbox_uuid = bluetooth.UUID(INBOX)

    if payload is None:
        ((inbox,),) = radio.gatts_register_services(
            ((service_uuid, ((inbox_uuid, bluetooth.FLAG_WRITE),)),)
        )
        advertisement = b"\x02\x01\x06\x11\x07" + bytes(service_uuid) + b"\x09\x09Mycelium"

        def advertise():
            radio.gap_advertise(250_000, adv_data=advertisement)

        def receive(event, data):
            if event == CENTRAL_DISCONNECT:
                advertise()
            elif event == GATTS_WRITE and data[1] == inbox:
                process_received_frame(radio.gatts_read(inbox))

        radio.irq(receive)
        advertise()
        print("Listening for Mycelium BLE writes on ESP32")
        try:
            while True:
                time.sleep_ms(100)
        finally:
            radio.active(False)
        return

    target = None
    scanned = False
    connection = None
    inbox = None
    discovery_status = None
    write_status = None
    disconnected = False
    service_bytes = bytes(service_uuid)

    def receive(event, data):
        nonlocal target, scanned, connection, inbox, discovery_status, write_status, disconnected
        if event == SCAN_RESULT and target is None:
            address_type, address, adv_type, _, raw = data
            address = bytes(address)
            if peer and address.hex().lower() != peer.replace(":", "").lower():
                return
            if adv_type in (0, 1, 4) and (service_bytes in bytes(raw) or b"Mycelium" in bytes(raw)):
                target = (address_type, address)
        elif event == SCAN_DONE:
            scanned = True
        elif event == PERIPHERAL_CONNECT:
            connection = data[0]
        elif event == PERIPHERAL_DISCONNECT:
            disconnected = True
        elif event == CHARACTERISTIC_RESULT and data[4] == inbox_uuid:
            inbox = data[2]
        elif event == CHARACTERISTIC_DONE:
            discovery_status = data[1]
        elif event == WRITE_DONE and data[1] == inbox:
            write_status = data[2]

    def wait_for(done, operation):
        deadline = time.ticks_add(time.ticks_ms(), 10000)
        while not done():
            if disconnected:
                raise RuntimeError("BLE peer disconnected during " + operation)
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise RuntimeError("Timed out during " + operation)
            time.sleep_ms(20)

    radio.irq(receive)
    try:
        radio.gap_scan(6000, 30000, 30000, True)
        wait_for(lambda: scanned, "scan")
        if target is None:
            raise RuntimeError("No Mycelium BLE listener found")
        radio.gap_connect(*target)
        wait_for(lambda: connection is not None, "connect")
        radio.gattc_discover_characteristics(connection, 1, 0xffff)
        wait_for(lambda: discovery_status is not None, "characteristic discovery")
        if discovery_status or inbox is None:
            raise RuntimeError("Mycelium write characteristic unavailable")
        radio.gattc_write(connection, inbox, payload, 1)
        wait_for(lambda: write_status is not None, "write")
        if write_status:
            raise RuntimeError(f"GATT write failed: {write_status}")
        print("GATT write accepted by", target[1].hex())
    finally:
        radio.active(False)


# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main(args=None):
    args = sys.argv[1:] if args is None else list(args)
    peer = None
    if args[:1] == ["--peer"] and len(args) >= 3:
        _, peer, *args = args

    if not args or args == ["listen"]:
        payload = None
    elif args == ["ping"]:
        # Pack a structured 20-byte Heartbeat
        payload = pack_packet(origin=MY_NODE_ID, target="*", ttl=1, hop_count=0, msg_type=TYPE_HEARTBEAT, payload="PING")
    elif args == ["sos"]:
        # Pack an Emergency SOS alert broadcast
        payload = pack_packet(origin=MY_NODE_ID, target="*", ttl=4, hop_count=0, msg_type=TYPE_SOS, payload="EMERGENCY SOS")
        print(f"[{MY_NODE_ID}] Generated Emergency SOS Broadcast Frame (20B)")
    elif args[:1] == ["send"] and len(args) >= 3:
        target_node = args[1]
        msg = " ".join(args[2:])
        payload = pack_packet(origin=MY_NODE_ID, target=target_node, ttl=4, hop_count=0, msg_type=TYPE_CHAT, payload=msg)
        print(f"[{MY_NODE_ID}] Packed 20B mesh frame -> Target: {target_node} | '{msg[:13]}'")
    elif args[:1] == ["send"] and len(args) == 2:
        # Broadcast chat
        msg = args[1]
        payload = pack_packet(origin=MY_NODE_ID, target="*", ttl=4, hop_count=0, msg_type=TYPE_CHAT, payload=msg)
        print(f"[{MY_NODE_ID}] Packed 20B broadcast mesh frame | '{msg[:13]}'")
    else:
        print("Mycelium BLE Mesh Usage:")
        print("  python3 ble.py listen")
        print("  python3 ble.py send <target_node_id> <message>")
        print("  python3 ble.py send <message> (Broadcast)")
        print("  python3 ble.py sos")
        print("  python3 ble.py ping")
        return

    if sys.implementation.name == "micropython":
        esp_main(payload, peer)
    else:
        import asyncio
        asyncio.run(linux_main(payload, peer))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n[!] {MY_NODE_ID} stopping.")
