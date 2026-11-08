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
    import logging
except ImportError:
    logging = None

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
        TYPE_ACK,
        TYPE_LINK_REPORT,
        TYPE_ROUTE_ADVERTISEMENT,
        TYPE_EVENT_REPORT,
        TYPE_EVENT_ACK,
        TYPE_SOS_CLEAR,
        TYPE_NAMES,
        BROADCAST_ID,
        EVENT_PACKET_RX,
        EVENT_PACKET_STATE,
        pack_sos_clear_payload,
        TOTAL_SIZE,
        MAX_PAYLOAD,
        unpack_link_report_payload,
        unpack_event_report_fragment,
        EventReportReassembler,
        unpack_mesh_event_record,
        pack_mesh_event_record,
        unpack_sos_clear_payload,
    )
except ImportError:
    # Minimal fallback for MicroPython
    TOTAL_SIZE = 20
    MAX_PAYLOAD = 9
    TYPE_HEARTBEAT = 0x01
    TYPE_SOS = 0x02
    TYPE_CHAT = 0x04
    TYPE_ACK = 0x05
    TYPE_LINK_REPORT = 0x06
    TYPE_ROUTE_ADVERTISEMENT = 0x07
    TYPE_EVENT_REPORT = 0x08
    TYPE_EVENT_ACK = 0x09
    TYPE_SOS_CLEAR = 0x0A
    BROADCAST_ID = 0xFFFF

    def pack_sos_clear_payload(alert_message_id):
        if not isinstance(alert_message_id, int) or not 0 <= alert_message_id <= 0xFFFFFFFF:
            raise ValueError("SOS alert message ID must be an unsigned 32-bit integer")
        return alert_message_id.to_bytes(4, "big") + b"\x00" * 5

NAME = "Mycelium"
SERVICE = "9cd10001-8ed5-4e55-932e-5c558747527a"
INBOX = "9cd10002-8ed5-4e55-932e-5c558747527a"
MAX_BYTES = 20
BRIDGE_REPORTER = None
EVENT_REPORT_REASSEMBLER = None

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

_configured_node_id = os.getenv("MYCELIUM_NODE_ID") or get_local_node_id()
if (
    not isinstance(_configured_node_id, str)
    or len(_configured_node_id) != 9
    or _configured_node_id[:5].lower() != "node-"
    or any(character not in "0123456789abcdefABCDEF" for character in _configured_node_id[5:])
):
    raise ValueError("MYCELIUM_NODE_ID must have the form Node-XXXX")
try:
    _configured_node_value = int(_configured_node_id[5:], 16)
    if _configured_node_value == BROADCAST_ID:
        raise ValueError("Node-FFFF is reserved for broadcast")
    MY_NODE_ID = f"Node-{_configured_node_value:04X}"
except ValueError as error:
    raise ValueError(f"Invalid MYCELIUM_NODE_ID: {error}") from error

# Set of recently seen packet signatures to prevent routing loops
SEEN_PACKETS = set()


def _log(level, message, *args):
    if logging is None:
        print(message % args if args else message)
        return
    getattr(logging.getLogger(__name__), level)(message, *args)


def emit_event(event_dict: dict):
    """Logs structured mesh events at DEBUG."""
    event_dict.setdefault("occurredAt", int(time.time() * 1000))
    _log(
        "debug",
        "BLE event: %s",
        json.dumps(event_dict, separators=(",", ":"), ensure_ascii=False),
    )

def process_received_frame(raw_data: bytes, forward_callback=None, deduplicate=True, event_callback=None):
    """Decodes a 20-byte BLE mesh frame and decides whether to consume or relay it."""
    data_bytes = bytes(raw_data)
    emit = event_callback or emit_event

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

            if deduplicate:
                sig = f"{origin}-{target}-{msg}"
                if sig in SEEN_PACKETS:
                    return
                SEEN_PACKETS.add(sig)
                if len(SEEN_PACKETS) > 500:
                    SEEN_PACKETS.pop()

            # Ignore packets originated by this node
            if origin == MY_NODE_ID:
                return

            if pkt["type_id"] == TYPE_LINK_REPORT:
                try:
                    report = unpack_link_report_payload(pkt["raw_payload"])
                except ValueError as error:
                    _log("warning", "Invalid BLE link report: %r", error)
                    return
                observed_at = report["observed_at"] * 1000
                link_event = {
                    "event": "LINK_UPDATE",
                    "source": origin,
                    "target": report["neighbor_id"],
                    "active": True,
                    "rssi": report["rssi"],
                    "reportId": f'{origin}:{pkt["message_id"]:08X}',
                    "observedAt": observed_at,
                    "sampleCount": report["sample_count"],
                }
                if "measurement_window_ms" in report:
                    link_event["rssiMedian"] = report["rssi"]
                    link_event["measurementWindowMs"] = report["measurement_window_ms"]
                    if report["rssi_mad"] is not None:
                        link_event["rssiMad"] = report["rssi_mad"]
                emit(link_event)
                return
            if pkt["type_id"] == TYPE_ROUTE_ADVERTISEMENT:
                return
            if pkt["type_id"] in (TYPE_EVENT_ACK,):
                return
            if pkt["type_id"] == TYPE_EVENT_REPORT:
                if target != MY_NODE_ID or BRIDGE_REPORTER is None or EVENT_REPORT_REASSEMBLER is None:
                    return False
                try:
                    report_id, event, complete = EVENT_REPORT_REASSEMBLER.add(
                        origin, pkt["raw_payload"]
                    )
                except ValueError as error:
                    _log("warning", "Invalid mesh event report: %r", error)
                    return False
                if not complete:
                    return False
                if event is not None:
                    try:
                        BRIDGE_REPORTER.send_mesh_event(event, origin)
                    except OSError as error:
                        EVENT_REPORT_REASSEMBLER.forget_completed(origin, report_id)
                        _log("warning", "Could not deliver mesh event to local bridge: %r", error)
                        return False
                return True
            related_message_id = 0
            if pkt["type_id"] == TYPE_SOS_CLEAR:
                try:
                    related_message_id = unpack_sos_clear_payload(pkt["raw_payload"])
                except ValueError as error:
                    _log("warning", "Invalid SOS clear packet: %r", error)
                    return False

            _log(
                "info",
                "BLE packet received origin=%s target=%s hops=%d ttl=%d type=%s message=%s",
                origin,
                target,
                hops,
                ttl,
                p_type,
                msg,
            )

            emit({
                "event": "PACKET_RX",
                "origin": origin,
                "target": target,
                "message_id": pkt["message_id"],
                "hops": hops,
                "ttl": ttl,
                "observer": MY_NODE_ID,
                "type": p_type,
                "message": msg,
                "related_message_id": related_message_id,
                "_message_bytes": pkt["raw_payload"].rstrip(b"\x00"),
            })

            # Check if this node is the intended destination
            if target == MY_NODE_ID or target == "*":
                _log("info", "Packet destination reached at %s", MY_NODE_ID)
                if p_type == "SOS":
                    _log("warning", "Emergency SOS received at %s", MY_NODE_ID)
            elif target != MY_NODE_ID and ttl > 1:
                # We are an intermediate relay node: Forward packet!
                _log("debug", "Relaying packet to %s with next TTL %d", target, ttl - 1)
                new_frame = pack_packet(
                    origin=origin,
                    target=target,
                    ttl=ttl - 1,
                    hop_count=hops + 1,
                    msg_type=pkt["type_id"],
                    payload=pkt["raw_payload"],
                    message_id=pkt["message_id"],
                )
                if forward_callback:
                    forward_callback(new_frame, target)

            return True
        except (ValueError, KeyError, TypeError) as error:
            _log("warning", "Invalid Mycelium frame: %r", error)

    # Fallback to plain UTF-8 string
    try:
        _log("info", "BLE raw message received: %s", data_bytes.decode("utf-8"))
    except UnicodeError:
        _log("warning", "BLE raw message contains invalid UTF-8: %r", data_bytes)


# ==============================================================================
# Linux / CPython Implementation (BlueZ + dbus-fast + Bleak)
# ==============================================================================

async def linux_main(payload, peer, forward_mode=False):
    import asyncio
    from peer import configure_logging

    configure_logging()
    if payload is not None:
        from bleak import BleakClient, BleakScanner

        peer_rssi = None

        def matches(device, advertisement):
            nonlocal peer_rssi
            if peer and device.address.lower() != peer.lower():
                return False
            is_match = bool(peer) or SERVICE in advertisement.service_uuids or advertisement.local_name == NAME
            if is_match:
                peer_rssi = advertisement.rssi
                # Stream RSSI of discovered peer to the 3D bridge
                clean_mac = device.address.replace(":", "").upper()
                peer_node_id = f"Node-{clean_mac[-4:]}"
                emit_event({
                    "event": "LINK_UPDATE",
                    "source": MY_NODE_ID,
                    "target": peer_node_id,
                    "rssi": peer_rssi,
                })
            return is_match

        _log("debug", "Scanning for Mycelium BLE peers")
        device = await BleakScanner.find_device_by_filter(matches, timeout=8.0)
        if device is None:
            raise RuntimeError("No Mycelium BLE peer found in radio range")

        _log("debug", "Connecting to peer %s (RSSI: %s dBm)", device.address, peer_rssi)
        write_confirmed = False
        try:
            async with BleakClient(device) as client:
                await client.write_gatt_char(INBOX, payload, response=True)
                write_confirmed = True
        except EOFError:
            if not write_confirmed:
                raise
            _log("warning", "BLE write succeeded but D-Bus closed during disconnect")
        _log("info", "BLE write confirmed by %s", device.address)
        return

    await linux_listen()


async def linux_listen(queue=None):
    global MY_NODE_ID, EVENT_REPORT_REASSEMBLER
    import asyncio
    from peer import configure_logging
    from dbus_fast import BusType, DBusError, PropertyAccess
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, dbus_property, method

    configure_logging()

    root = "/org/mycelium"
    service_path = root + "/service0"
    advertisement_path = "/org/mycelium_advertisement"
    gatt_manager = "org.bluez.GattManager1"
    advertising_manager = "org.bluez.LEAdvertisingManager1"

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
            if queue is None or len(value) != TOTAL_SIZE:
                process_received_frame(value, forward_callback=do_relay_forward)
            else:
                device = options.get("device")
                path = device.value if device is not None else ""
                address = path.rsplit("/dev_", 1)[-1].replace("_", ":") if "/dev_" in path else None
                queue.accept(bytes(value), incoming=True, source=address)

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
            _log("debug", "Mycelium BLE advertisement released")

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
        await adapter.get_interface(advertising_manager).call_register_advertisement(advertisement_path, {})
        local_address = objects[adapter_path]["org.bluez.Adapter1"]["Address"].value.upper()
        if not os.getenv("MYCELIUM_NODE_ID"):
            clean_address = local_address.replace(":", "")
            MY_NODE_ID = f"Node-{clean_address[-4:]}"
            if queue is not None:
                queue.set_node_id(MY_NODE_ID)
        if queue is not None:
            from peer import canonical_node_id
            from protocol import (
                EVENT_PACKET_RX,
                EVENT_PACKET_STATE,
                TYPE_HEARTBEAT,
                TYPE_SOS,
                TYPE_TELEMETRY,
                TYPE_CHAT,
                TYPE_ACK,
                TYPE_SOS_CLEAR,
                pack_mesh_event_record,
            )

            gateway_node_id = os.getenv("MYCELIUM_GATEWAY_NODE_ID") or "Node-0000"
            try:
                gateway_node_id = canonical_node_id(gateway_node_id)
            except ValueError as error:
                raise RuntimeError(f"Invalid MYCELIUM_GATEWAY_NODE_ID: {error}") from error
            queue.set_gateway_node_id(gateway_node_id)
            EVENT_REPORT_REASSEMBLER = EventReportReassembler()
            packet_types = {
                "HEARTBEAT": TYPE_HEARTBEAT,
                "SOS": TYPE_SOS,
                "TELEMETRY": TYPE_TELEMETRY,
                "CHAT": TYPE_CHAT,
                "ACK": TYPE_ACK,
                "SOS_CLEAR": TYPE_SOS_CLEAR,
            }

            def handle_peer_event(event):
                event.setdefault("occurredAt", int(time.time() * 1000))
                event_type = event.get("event")
                if event_type in {"PACKET_RX", "PACKET_STATE"}:
                    packet = event.get("packet", event)
                    packet_type = packet_types.get(
                        packet.get("type") if event_type == "PACKET_RX"
                        else (packet.get("payload") or {}).get("type")
                    )
                    if packet_type is not None:
                        message = event.get("_message_bytes")
                        if message is None:
                            message = (
                                (packet.get("message") or "").encode("utf-8")
                                if event_type == "PACKET_RX"
                                else ((packet.get("payload") or {}).get("message") or "").encode("utf-8")
                            )
                        if packet_type == TYPE_SOS_CLEAR:
                            message = b""
                        message = message[:MAX_PAYLOAD]
                        current_hop = packet.get("currentHop") or {}
                        record = pack_mesh_event_record(
                            event_kind=(
                                EVENT_PACKET_RX if event_type == "PACKET_RX"
                                else EVENT_PACKET_STATE
                            ),
                            origin=packet.get("origin"),
                            target=packet.get("target"),
                            message_id=packet.get("message_id", packet.get("messageId", 0)),
                            packet_type=packet_type,
                            ttl=packet.get("ttl", 0),
                            hop_count=packet.get("hops", packet.get("hop_count", 0)),
                            observed_at=event["occurredAt"] // 1000,
                            message=message,
                            status=None if event_type == "PACKET_RX" else packet.get("status"),
                            related_message_id=packet.get("related_message_id", 0),
                            hop_source=current_hop.get("source"),
                        )
                        if queue.node_id == gateway_node_id:
                            if BRIDGE_REPORTER is not None:
                                try:
                                    BRIDGE_REPORTER.send_mesh_event(
                                        unpack_mesh_event_record(record), queue.node_id
                                    )
                                except OSError as error:
                                    _log("warning", "Could not deliver local mesh event: %r", error)
                        elif gateway_node_id:
                            reliable = packet_type in (TYPE_SOS, TYPE_SOS_CLEAR)
                            if not queue.queue_event_report(record, reliable=reliable):
                                _log("warning", "Could not queue mesh event for gateway %s", gateway_node_id)
                safe_event = {key: value for key, value in event.items() if not key.startswith("_")}
                emit_event(safe_event)

            queue.on_event = handle_peer_event

        emit_event({"event": "NODE_ANNOUNCE", "node_id": MY_NODE_ID, "mac": local_address})
        _log("info", "Mycelium node %s; BLE address %s", MY_NODE_ID, local_address)

        if queue is None:
            _log("info", "Listening for Mycelium BLE writes")
            await bus.wait_for_disconnect()
        else:
            from peer import run

            peer_task = asyncio.create_task(run(queue, SERVICE, INBOX, local_address))
            bus_task = asyncio.create_task(bus.wait_for_disconnect())
            active_tasks = [peer_task, bus_task]
            try:
                done, _ = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in active_tasks:
                    task.cancel()
                await asyncio.gather(*active_tasks, return_exceptions=True)
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
        _log("info", "GATT write accepted by %s", target[1].hex())
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

    if args == ["run"]:
        if sys.implementation.name != "cpython" or sys.platform != "linux":
            raise SystemExit("The persistent peer mode requires Linux")
        import asyncio
        from peer import PeerQueue

        queue_holder = {}

        def receive_chat(frame):
            return process_received_frame(
                frame,
                deduplicate=False,
                event_callback=queue_holder["queue"].on_event,
            )

        queue = PeerQueue(MY_NODE_ID, receive_chat, emit_event)
        queue_holder["queue"] = queue
        asyncio.run(linux_listen(queue))
        return

    if not args or args == ["listen"]:
        payload = None
    elif args == ["ping"]:
        # Pack a structured 20-byte Heartbeat
        payload = pack_packet(origin=MY_NODE_ID, target="*", ttl=1, hop_count=0, msg_type=TYPE_HEARTBEAT, payload="PING")
    elif args == ["sos"]:
        # Pack an Emergency SOS alert broadcast
        payload = pack_packet(origin=MY_NODE_ID, target="*", ttl=4, hop_count=0, msg_type=TYPE_SOS, payload="SOS")
        message_id = unpack_packet(payload)["message_id"]
        print(f"[{MY_NODE_ID}] Generated SOS message ID 0x{message_id:08X} (20B frame)")
    elif args[:1] == ["sos-clear"] and len(args) == 2:
        try:
            alert_message_id = int(args[1], 0)
        except ValueError:
            try:
                alert_message_id = int(args[1], 16)
            except ValueError as error:
                raise SystemExit("SOS message ID must be decimal or hexadecimal") from error
        payload = pack_packet(
            origin=MY_NODE_ID,
            target="*",
            ttl=4,
            msg_type=TYPE_SOS_CLEAR,
            payload=pack_sos_clear_payload(alert_message_id),
        )
        print(f"[{MY_NODE_ID}] Generated SOS_CLEAR for 0x{alert_message_id:08X} (20B frame)")
    elif args[:1] == ["send"] and len(args) >= 3:
        target_node = args[1]
        msg = " ".join(args[2:])
        payload = pack_packet(origin=MY_NODE_ID, target=target_node, ttl=4, hop_count=0, msg_type=TYPE_CHAT, payload=msg)
        print(f"[{MY_NODE_ID}] Packed 20B mesh frame -> Target: {target_node} | '{msg[:MAX_PAYLOAD]}'")
    elif args[:1] == ["send"] and len(args) == 2:
        # Broadcast chat
        msg = args[1]
        payload = pack_packet(origin=MY_NODE_ID, target="*", ttl=4, hop_count=0, msg_type=TYPE_CHAT, payload=msg)
        print(f"[{MY_NODE_ID}] Packed 20B broadcast mesh frame | '{msg[:MAX_PAYLOAD]}'")
    else:
        print("Mycelium BLE Mesh Usage:")
        print("  python3 ble.py run")
        print("  python3 ble.py listen")
        print("  python3 ble.py send <target_node_id> <message>")
        print("  python3 ble.py send <message> (Broadcast)")
        print("  python3 ble.py sos")
        print("  python3 ble.py sos-clear <sos_message_id>")
        print("  python3 ble.py ping")
        return

    if sys.implementation.name == "micropython":
        esp_main(payload, peer)
    else:
        import asyncio
        async def dispatch():
            if payload is not None and peer is None and sys.platform == "linux":
                from peer import enqueue

                if await enqueue(payload):
                    print("Queued on local Mycelium peer")
                    return
            await linux_main(payload, peer)

        asyncio.run(dispatch())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n[!] {MY_NODE_ID} stopping.")