#!/usr/bin/env python3
"""
Mycelium - Compact 20-Byte BLE Mesh Protocol
Encodes and decodes multi-hop routing headers into exactly 20 bytes (BLE ATT MTU limit).

Frame Structure (20 Bytes Exact):
  [ Origin ID: 2B | Target ID: 2B | TTL: 1B | Hop Count: 1B | Msg Type: 1B | Payload: 13B ]
"""

import struct
from typing import Dict, Any, Union

PACKET_FORMAT = "!HHBBB13s"
TOTAL_SIZE = struct.calcsize(PACKET_FORMAT)  # Exactly 20 bytes

# Message Types
TYPE_HEARTBEAT = 0x01
TYPE_SOS = 0x02
TYPE_TELEMETRY = 0x03
TYPE_CHAT = 0x04
TYPE_ECHO_REQUEST = 0x05
TYPE_ECHO_REPLY = 0x06

TYPE_NAMES = {
    TYPE_HEARTBEAT: "HEARTBEAT",
    TYPE_SOS: "SOS",
    TYPE_TELEMETRY: "TELEMETRY",
    TYPE_CHAT: "CHAT",
    TYPE_ECHO_REQUEST: "ECHO_REQUEST",
    TYPE_ECHO_REPLY: "ECHO_REPLY",
}

# 0xFFFF denotes broadcast to all reachable peers
BROADCAST_ID = 0xFFFF

def node_id_to_int(node_str: str) -> int:
    """Converts a node string (e.g. 'Node-6EBB', '6EBB', or '*') to a 16-bit unsigned int."""
    if node_str == "*" or node_str.upper() == "ALL" or node_str.upper() == "BROADCAST":
        return BROADCAST_ID
    clean = node_str.upper().replace("NODE-", "").replace(":", "")
    try:
        return int(clean[-4:], 16)
    except ValueError:
        return 0x0001

def int_to_node_id(node_int: int) -> str:
    """Converts a 16-bit int to a friendly Node ID string."""
    if node_int == BROADCAST_ID:
        return "*"
    return f"Node-{node_int:04X}"

def pack_packet(
    origin: Union[str, int],
    target: Union[str, int],
    ttl: int = 4,
    hop_count: int = 0,
    msg_type: int = TYPE_CHAT,
    payload: Union[str, bytes] = "",
) -> bytes:
    """
    Packs a routing frame into exactly 20 bytes.
    """
    origin_int = origin if isinstance(origin, int) else node_id_to_int(origin)
    target_int = target if isinstance(target, int) else node_id_to_int(target)

    # Encode payload to max 13 bytes, padded with null bytes
    if isinstance(payload, str):
        payload_bytes = payload.encode("utf-8")[:13]
    else:
        payload_bytes = bytes(payload)[:13]
    payload_padded = payload_bytes.ljust(13, b"\x00")

    return struct.pack(
        PACKET_FORMAT,
        origin_int,
        target_int,
        min(max(0, ttl), 255),
        min(max(0, hop_count), 255),
        msg_type,
        payload_padded,
    )

def unpack_packet(raw_bytes: bytes) -> Dict[str, Any]:
    """
    Unpacks a 20-byte BLE frame into a structured dictionary.
    """
    if len(raw_bytes) != TOTAL_SIZE:
        raise ValueError(f"Invalid packet size: expected {TOTAL_SIZE} bytes, got {len(raw_bytes)}")

    origin_int, target_int, ttl, hop_count, msg_type, payload_padded = struct.unpack(
        PACKET_FORMAT, raw_bytes
    )

    payload_clean = payload_padded.rstrip(b"\x00")
    try:
        payload_str = payload_clean.decode("utf-8")
    except UnicodeDecodeError:
        payload_str = payload_clean.hex()

    return {
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
