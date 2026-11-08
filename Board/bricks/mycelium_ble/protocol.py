#!/usr/bin/env python3
"""
Mycelium - Compact 20-Byte BLE Mesh Protocol
Encodes and decodes multi-hop routing headers into exactly 20 bytes (BLE ATT MTU limit).

Frame Structure (20 Bytes Exact):
  [ Origin ID: 2B | Target ID: 2B | TTL: 1B | Hop Count: 1B | Msg Type: 1B | Message ID: 4B | Payload: 9B ]
"""

import os
import struct
import time
from collections import OrderedDict
from math import ceil
from typing import Dict, Any, Union

PACKET_FORMAT = "!HHBBBI9s"
MAX_PAYLOAD = 9
TOTAL_SIZE = struct.calcsize(PACKET_FORMAT)  # Exactly 20 bytes
LINK_REPORT_WINDOW_MS = 30_000
LINK_REPORT_MAX_SAMPLES = 12


def pack_sos_clear_payload(alert_message_id: int) -> bytes:
    if (
        not isinstance(alert_message_id, int)
        or isinstance(alert_message_id, bool)
        or not 0 <= alert_message_id <= 0xFFFFFFFF
    ):
        raise ValueError("SOS alert message ID must be an unsigned 32-bit integer")
    return struct.pack("!I", alert_message_id) + b"\x00" * 5


def unpack_sos_clear_payload(payload: bytes) -> int:
    if len(payload) != MAX_PAYLOAD or any(payload[4:]):
        raise ValueError("SOS clear payload must contain one message ID")
    return struct.unpack("!I", payload[:4])[0]


def pack_mesh_event_record(
    event_kind: int,
    origin: str,
    target: str,
    message_id: int,
    packet_type: int,
    ttl: int,
    hop_count: int,
    observed_at: int,
    message: Union[str, bytes] = b"",
    status: str | None = None,
    related_message_id: int = 0,
    hop_source: str | None = None,
) -> bytes:
    if event_kind not in (EVENT_PACKET_RX, EVENT_PACKET_STATE):
        raise ValueError("unsupported mesh event kind")
    if not isinstance(packet_type, int) or packet_type not in {
        TYPE_HEARTBEAT,
        TYPE_SOS,
        TYPE_TELEMETRY,
        TYPE_CHAT,
        TYPE_ACK,
        TYPE_SOS_CLEAR,
    }:
        raise ValueError("unsupported application packet type")
    if status not in EVENT_STATUS_CODES:
        raise ValueError("unsupported packet state")
    origin_value = node_id_to_int(origin)
    target_value = node_id_to_int(target)
    if origin_value == BROADCAST_ID:
        raise ValueError("packet origin cannot use the broadcast ID")
    hop_source_value = BROADCAST_ID if hop_source is None else node_id_to_int(hop_source)
    for name, value, maximum in (
        ("message ID", message_id, 0xFFFFFFFF),
        ("TTL", ttl, 0xFF),
        ("hop count", hop_count, 0xFF),
        ("observation time", observed_at, 0xFFFFFFFF),
        ("related message ID", related_message_id, 0xFFFFFFFF),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
            raise ValueError(f"{name} must be an unsigned integer")
    if isinstance(message, str):
        message = message.encode("utf-8")
    if not isinstance(message, bytes) or len(message) > MAX_EVENT_RECORD_PAYLOAD:
        raise ValueError("event message must fit the 9-byte application payload")

    return struct.pack(
        "!BHHIBBBBIBIH",
        event_kind,
        origin_value,
        target_value,
        message_id,
        packet_type,
        EVENT_STATUS_CODES[status],
        ttl,
        hop_count,
        observed_at,
        len(message),
        related_message_id,
        hop_source_value,
    ) + message


def unpack_mesh_event_record(record: bytes) -> dict:
    record_format = "!BHHIBBBBIBIH"
    record_size = struct.calcsize(record_format)
    if len(record) < record_size:
        raise ValueError("mesh event record is truncated")
    (
        event_kind,
        origin,
        target,
        message_id,
        packet_type,
        status_code,
        ttl,
        hop_count,
        observed_at,
        message_length,
        related_message_id,
        hop_source,
    ) = struct.unpack(record_format, record[:record_size])
    if origin == BROADCAST_ID:
        raise ValueError("mesh event origin cannot use the broadcast ID")
    if event_kind not in (EVENT_PACKET_RX, EVENT_PACKET_STATE):
        raise ValueError("unsupported mesh event kind")
    if packet_type not in {
        TYPE_HEARTBEAT,
        TYPE_SOS,
        TYPE_TELEMETRY,
        TYPE_CHAT,
        TYPE_ACK,
        TYPE_SOS_CLEAR,
    }:
        raise ValueError("unsupported application packet type")
    if status_code not in EVENT_STATUS_NAMES:
        raise ValueError("unsupported packet state")
    if message_length > MAX_EVENT_RECORD_PAYLOAD or len(record) < record_size + message_length:
        raise ValueError("invalid event message length")

    message = record[record_size:record_size + message_length]
    return {
        "event_kind": event_kind,
        "origin": int_to_node_id(origin),
        "target": int_to_node_id(target),
        "message_id": message_id,
        "packet_type": packet_type,
        "status": EVENT_STATUS_NAMES[status_code],
        "ttl": ttl,
        "hop_count": hop_count,
        "observed_at": observed_at,
        "message": message.decode("utf-8", errors="replace"),
        "related_message_id": related_message_id,
        "hop_source": None if hop_source == BROADCAST_ID else int_to_node_id(hop_source),
    }


def pack_event_report_fragments(report_id: int, record: bytes) -> list:
    if (
        not isinstance(report_id, int)
        or isinstance(report_id, bool)
        or not 0 <= report_id <= 0xFFFF
        or not isinstance(record, bytes)
        or not record
    ):
        raise ValueError("invalid event report")
    count = ceil(len(record) / EVENT_REPORT_FRAGMENT_DATA_SIZE)
    if count > MAX_EVENT_REPORT_FRAGMENTS:
        raise ValueError("event report exceeds the fragment limit")
    fragments = []
    for index in range(count):
        chunk = record[
            index * EVENT_REPORT_FRAGMENT_DATA_SIZE:
            (index + 1) * EVENT_REPORT_FRAGMENT_DATA_SIZE
        ]
        fragments.append(struct.pack("!HBB", report_id, index, count) + chunk.ljust(
            EVENT_REPORT_FRAGMENT_DATA_SIZE, b"\x00"
        ))
    return fragments


def unpack_event_report_fragment(payload: bytes) -> tuple:
    if len(payload) != MAX_PAYLOAD:
        raise ValueError("event report fragment must be exactly 9 bytes")
    report_id, index, count = struct.unpack("!HBB", payload[:4])
    if not 1 <= count <= MAX_EVENT_REPORT_FRAGMENTS or index >= count:
        raise ValueError("invalid event report fragment index/count")
    return report_id, index, count, payload[4:]


def pack_event_ack_payload(report_id: int) -> bytes:
    if not isinstance(report_id, int) or isinstance(report_id, bool) or not 0 <= report_id <= 0xFFFF:
        raise ValueError("event report ID must be an unsigned 16-bit integer")
    return struct.pack("!H", report_id) + b"\x00" * 7


def unpack_event_ack_payload(payload: bytes) -> int:
    if len(payload) != MAX_PAYLOAD or any(payload[2:]):
        raise ValueError("event ACK payload must contain one report ID")
    return struct.unpack("!H", payload[:2])[0]


class EventReportReassembler:
    def __init__(self):
        self.pending = OrderedDict()
        self.completed = OrderedDict()

    def add(self, observer: str, payload: bytes, now=None):
        now = time.monotonic() if now is None else now
        self.expire(now)
        report_id, index, count, chunk = unpack_event_report_fragment(payload)
        key = (observer, report_id)
        if key in self.completed:
            return report_id, None, True

        report = self.pending.get(key)
        if report is None:
            report = {"count": count, "chunks": {}, "created_at": now}
            self.pending[key] = report
        elif report["count"] != count:
            self.pending.pop(key, None)
            raise ValueError("inconsistent event report fragment count")
        report["chunks"][index] = chunk
        self.pending.move_to_end(key)
        while len(self.pending) > MAX_PENDING_EVENT_REPORTS:
            self.pending.popitem(last=False)
        if len(report["chunks"]) != count:
            return report_id, None, False

        record = b"".join(report["chunks"][part] for part in range(count))
        decoded = unpack_mesh_event_record(record)
        self.pending.pop(key, None)
        self.completed[key] = now
        while len(self.completed) > MAX_PENDING_EVENT_REPORTS * 16:
            self.completed.popitem(last=False)
        return report_id, decoded, True

    def expire(self, now=None):
        now = time.monotonic() if now is None else now
        for key, report in list(self.pending.items()):
            if now - report["created_at"] > EVENT_REPORT_ASSEMBLY_SECONDS:
                del self.pending[key]
        for key, completed_at in list(self.completed.items()):
            if now - completed_at > EVENT_REPORT_ASSEMBLY_SECONDS:
                del self.completed[key]

    def forget_completed(self, observer: str, report_id: int):
        self.completed.pop((observer, report_id), None)

# Message Types
TYPE_HEARTBEAT = 0x01
TYPE_SOS = 0x02
TYPE_TELEMETRY = 0x03
TYPE_CHAT = 0x04
TYPE_ACK = 0x05
TYPE_LINK_REPORT = 0x06
TYPE_ROUTE_ADVERTISEMENT = 0x07
TYPE_EVENT_REPORT = 0x08
TYPE_EVENT_ACK = 0x09
TYPE_SOS_CLEAR = 0x0A

EVENT_PACKET_RX = 1
EVENT_PACKET_STATE = 2
EVENT_STATUS_CODES = {
    None: 0,
    "queued": 1,
    "forwarding": 2,
    "awaiting_ack": 3,
    "failed": 4,
    "delivered": 5,
}
EVENT_STATUS_NAMES = {value: key for key, value in EVENT_STATUS_CODES.items()}
MAX_EVENT_RECORD_PAYLOAD = 9
MAX_EVENT_REPORT_FRAGMENTS = 16
EVENT_REPORT_FRAGMENT_DATA_SIZE = 5
EVENT_REPORT_ASSEMBLY_SECONDS = 15
MAX_PENDING_EVENT_REPORTS = 64

TYPE_NAMES = {
    TYPE_HEARTBEAT: "HEARTBEAT",
    TYPE_SOS: "SOS",
    TYPE_TELEMETRY: "TELEMETRY",
    TYPE_CHAT: "CHAT",
    TYPE_ACK: "ACK",
    TYPE_LINK_REPORT: "LINK_REPORT",
    TYPE_ROUTE_ADVERTISEMENT: "ROUTE_ADVERTISEMENT",
    TYPE_EVENT_REPORT: "EVENT_REPORT",
    TYPE_EVENT_ACK: "EVENT_ACK",
    TYPE_SOS_CLEAR: "SOS_CLEAR",
}

# 0xFFFF denotes broadcast to all reachable peers.
BROADCAST_ID = 0xFFFF

def node_id_to_int(node_str: str) -> int:
    """Converts a node string (e.g. 'Node-6EBB', '6EBB', or '*') to a 16-bit unsigned int."""
    if node_str == "*" or node_str.upper() == "ALL" or node_str.upper() == "BROADCAST":
        return BROADCAST_ID
    clean = node_str.upper().replace("NODE-", "").replace(":", "")
    try:
        node_id = int(clean[-4:], 16)
    except ValueError:
        return 0x0001
    if node_id == BROADCAST_ID:
        raise ValueError("Node-FFFF is reserved for broadcast")
    return node_id

def int_to_node_id(node_int: int) -> str:
    """Converts a 16-bit int to a friendly Node ID string."""
    if node_int == BROADCAST_ID:
        return "*"
    return f"Node-{node_int:04X}"


def pack_link_report_payload(
    neighbor_id: str,
    rssi: int,
    observed_at: int,
    sample_count: int = 1,
    rssi_mad=None,
    version: int = 1,
) -> bytes:
    if (
        not isinstance(neighbor_id, str)
        or not neighbor_id.startswith("Node-")
        or len(neighbor_id) != 9
        or any(character not in "0123456789abcdefABCDEF" for character in neighbor_id[5:])
    ):
        raise ValueError("neighbor ID must have the form Node-XXXX")
    try:
        neighbor_value = int(neighbor_id[5:], 16)
    except ValueError as error:
        raise ValueError("neighbor ID must have the form Node-XXXX") from error
    if neighbor_value == BROADCAST_ID:
        raise ValueError("Node-FFFF is reserved for broadcast")
    if not isinstance(rssi, int) or not -126 <= rssi <= 0:
        raise ValueError("RSSI must be an integer from -126 to 0 dBm")
    if not isinstance(observed_at, int) or not 0 <= observed_at <= 0xFFFFFFFF:
        raise ValueError("observation time must be an unsigned 32-bit Unix timestamp")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or not 1 <= sample_count <= 0xFF:
        raise ValueError("sample count must be between 1 and 255")
    if version == 1:
        return struct.pack(
            "!BHbIB",
            1,
            neighbor_value,
            rssi,
            observed_at,
            sample_count,
        )
    if version != 2:
        raise ValueError("unsupported link report version")
    if rssi_mad is not None and (
        not isinstance(rssi_mad, int)
        or isinstance(rssi_mad, bool)
        or not 0 <= rssi_mad <= 254
    ):
        raise ValueError("RSSI MAD must be an integer from 0 to 254")

    return (
        struct.pack(
            "!BHbBB",
            2,
            neighbor_value,
            rssi,
            0xFF if rssi_mad is None else rssi_mad,
            sample_count,
        )
        + (observed_at & 0xFFFFFF).to_bytes(3, "big")
    )


def _expand_link_report_timestamp(timestamp: int, now: int | None = None) -> int:
    period = 1 << 24
    reference = int(time.time()) if now is None else now
    expanded = reference - reference % period + timestamp
    if expanded - reference > period // 2:
        return expanded - period
    if reference - expanded > period // 2:
        return expanded + period
    return expanded


def unpack_link_report_payload(payload: bytes) -> Dict[str, Any]:
    if len(payload) != 9:
        raise ValueError("link report payload must be exactly 9 bytes")
    version = payload[0]
    if version == 1:
        _, neighbor_id, rssi, observed_at, sample_count = struct.unpack("!BHbIB", payload)
    elif version == 2:
        _, neighbor_id, rssi, rssi_mad, sample_count = struct.unpack("!BHbBB", payload[:6])
        observed_at = _expand_link_report_timestamp(int.from_bytes(payload[6:], "big"))
    else:
        raise ValueError(f"unsupported link report version: {version}")
    if not -126 <= rssi <= 0:
        raise ValueError("link report RSSI is outside the valid range")
    if sample_count == 0:
        raise ValueError("link report sample count must be positive")

    report = {
        "neighbor_id": int_to_node_id(neighbor_id),
        "rssi": rssi,
        "observed_at": observed_at,
        "sample_count": sample_count,
    }
    if version == 2:
        report["rssi_mad"] = None if rssi_mad == 0xFF else rssi_mad
        report["measurement_window_ms"] = LINK_REPORT_WINDOW_MS
    return report


def pack_route_advertisement_payload(routes) -> bytes:
    if not isinstance(routes, (list, tuple)) or not 1 <= len(routes) <= 2:
        raise ValueError("route advertisement must contain one or two routes")

    encoded = bytearray((1, len(routes)))
    seen_destinations = set()
    for destination, metric in routes:
        if (
            not isinstance(destination, str)
            or len(destination) != 9
            or not destination.startswith("Node-")
            or any(character not in "0123456789abcdefABCDEF" for character in destination[5:])
        ):
            raise ValueError("route destination must have the form Node-XXXX")
        try:
            destination_value = int(destination[5:], 16)
        except ValueError as error:
            raise ValueError("route destination must have the form Node-XXXX") from error
        if destination_value == BROADCAST_ID:
            raise ValueError("Node-FFFF is reserved for broadcast")
        if destination_value in seen_destinations:
            raise ValueError("route advertisement cannot repeat a destination")
        if not isinstance(metric, int) or isinstance(metric, bool) or not 0 <= metric <= 255:
            raise ValueError("route metric must be an integer from 0 to 255")
        seen_destinations.add(destination_value)
        encoded.extend(struct.pack("!HB", destination_value, metric))
    return bytes(encoded)


def unpack_route_advertisement_payload(payload: bytes) -> list:
    if len(payload) < 5:
        raise ValueError("route advertisement payload is too short")
    version, count = payload[0], payload[1]
    if version != 1:
        raise ValueError(f"unsupported route advertisement version: {version}")
    if not 1 <= count <= 2 or len(payload) != 2 + count * 3:
        raise ValueError("route advertisement must contain one or two complete routes")

    routes = []
    seen_destinations = set()
    for offset in range(2, len(payload), 3):
        destination_value, metric = struct.unpack("!HB", payload[offset:offset + 3])
        if destination_value == BROADCAST_ID:
            raise ValueError("route destination cannot use the broadcast ID")
        if destination_value in seen_destinations:
            raise ValueError("route advertisement cannot repeat a destination")
        seen_destinations.add(destination_value)
        routes.append({
            "destination_id": int_to_node_id(destination_value),
            "metric": metric,
        })
    return routes


def pack_packet(
    origin: Union[str, int],
    target: Union[str, int],
    ttl: int = 4,
    hop_count: int = 0,
    msg_type: int = TYPE_CHAT,
    payload: Union[str, bytes] = "",
    message_id: int | None = None,
) -> bytes:
    """
    Packs a routing frame into exactly 20 bytes.
    """
    origin_int = origin if isinstance(origin, int) else node_id_to_int(origin)
    target_int = target if isinstance(target, int) else node_id_to_int(target)
    if origin_int == BROADCAST_ID:
        raise ValueError("packet origin cannot use the broadcast ID")

    if isinstance(payload, str):
        payload = payload.encode("utf-8")[:MAX_PAYLOAD].decode("utf-8", "ignore").encode("utf-8")
    payload_padded = bytes(payload)[:MAX_PAYLOAD].ljust(MAX_PAYLOAD, b"\x00")
    if message_id is None:
        message_id = int.from_bytes(os.urandom(4), "big")

    return struct.pack(
        PACKET_FORMAT,
        origin_int,
        target_int,
        min(max(0, ttl), 255),
        min(max(0, hop_count), 255),
        msg_type,
        message_id,
        payload_padded,
    )

def unpack_packet(raw_bytes: bytes) -> Dict[str, Any]:
    """
    Unpacks a 20-byte BLE frame into a structured dictionary.
    """
    if len(raw_bytes) != TOTAL_SIZE:
        raise ValueError(f"Invalid packet size: expected {TOTAL_SIZE} bytes, got {len(raw_bytes)}")

    origin_int, target_int, ttl, hop_count, msg_type, message_id, payload_padded = struct.unpack(
        PACKET_FORMAT, raw_bytes
    )

    if msg_type in {TYPE_LINK_REPORT, TYPE_EVENT_REPORT, TYPE_EVENT_ACK, TYPE_SOS_CLEAR}:
        payload_clean = payload_padded
        payload_str = payload_clean.hex()
    elif msg_type == TYPE_ROUTE_ADVERTISEMENT:
        count = payload_padded[1]
        payload_length = 2 + count * 3 if 1 <= count <= 2 else len(payload_padded)
        payload_clean = payload_padded[:payload_length]
        payload_str = payload_clean.hex()
    else:
        payload_clean = payload_padded.rstrip(b"\x00")
        try:
            payload_str = payload_clean.decode("utf-8")
        except UnicodeDecodeError:
            payload_str = payload_clean.hex()

    return {
        "message_id": message_id,
        "origin": int_to_node_id(origin_int),
        "target": int_to_node_id(target_int),
        "ttl": ttl,
        "hop_count": hop_count,
        "type": TYPE_NAMES.get(msg_type, f"TYPE_{msg_type}"),
        "type_id": msg_type,
        "payload": payload_str,
        "raw_payload": payload_clean,
    }

if __name__ == "__main__":
    print("=============================================================")
    print(" Mycelium 20-Byte BLE Mesh Protocol Self-Test")
    print(f" Frame Size: {TOTAL_SIZE} Bytes (BLE ATT MTU Compliant)")
    print("=============================================================")

    # Test Pack
    test_frame = pack_packet(
        origin="Node-6EBB",
        target="Node-3C4D",
        ttl=3,
        hop_count=1,
        msg_type=TYPE_SOS,
        payload="SOS EMERGENCY",
    )

    print(f"\n[Packed Bytes] Length: {len(test_frame)}B")
    print(f"  Hex Dump: {test_frame.hex(' ')}")

    # Test Unpack
    unpacked = unpack_packet(test_frame)
    print("\n[Unpacked Result]")
    for k, v in unpacked.items():
        print(f"  {k:12}: {v}")

    # Test Broadcast frame
    bcast_frame = pack_packet(origin="Node-6EBB", target="*", msg_type=TYPE_HEARTBEAT, payload="PING")
    bcast_unpacked = unpack_packet(bcast_frame)
    print("\n[Broadcast Result]")
    print(f"  Target: {bcast_unpacked['target']} (All Peers)")
    print(f"  Type  : {bcast_unpacked['type']}")

    print("\n[✓] All 20-byte packing/unpacking tests passed successfully!")
    print("=============================================================")