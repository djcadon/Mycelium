#!/usr/bin/env python3
"""
Mycelium - Pure Layer-2 Radio Transceiver (Zero IP)
Transmits and receives raw Ethernet frames directly over wlan0 using AF_PACKET.
Requires root privileges (run with sudo) or CAP_NET_RAW on the Python binary.

Usage:
  sudo python3 mesh_test.py listen
  sudo python3 mesh_test.py send "Your message here"
  sudo python3 mesh_test.py ping
"""

import sys
import socket
import struct
import json
import time
import threading

INTERFACE = "wlan0"
# IEEE 802 Local Experimental EtherType 1
ETH_P_MYCELIUM = 0x88B5
BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"
# Ethernet minimum frame size (excluding FCS which the NIC appends)
MIN_FRAME_SIZE = 60
# Maximum payload size (Ethernet MTU minus our 14-byte header)
MAX_PAYLOAD_SIZE = 1486

# ---------------------------------------------------------------------------
# Lazy initialization: MY_MAC and MY_NODE_ID are resolved on first use,
# not at module import time. This prevents crashes if wlan0 isn't ready yet.
# ---------------------------------------------------------------------------
_my_mac: bytes | None = None
_my_node_id: str | None = None
_send_sock: socket.socket | None = None


def _ensure_initialized():
    """Lazily initializes MAC address and Node ID on first call."""
    global _my_mac, _my_node_id
    if _my_mac is not None:
        return

    max_retries = 5
    for attempt in range(max_retries):
        try:
            _my_mac = get_mac_bytes()
            _my_node_id = get_node_id(_my_mac)
            return
        except FileNotFoundError:
            if attempt < max_retries - 1:
                print(f"[!] {INTERFACE} not ready, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(2)
            else:
                raise RuntimeError(
                    f"Could not read MAC address from {INTERFACE} after {max_retries} attempts. "
                    "Is the ad-hoc network set up? Run setup_adhoc.sh first."
                )


def get_my_mac() -> bytes:
    _ensure_initialized()
    assert _my_mac is not None
    return _my_mac


def get_my_node_id() -> str:
    _ensure_initialized()
    assert _my_node_id is not None
    return _my_node_id


def get_mac_bytes(ifname: str = INTERFACE) -> bytes:
    """Reads the 6-byte hardware MAC address of the interface."""
    with open(f"/sys/class/net/{ifname}/address", "r") as f:
        mac_str = f.read().strip()
    return bytes.fromhex(mac_str.replace(":", ""))


def format_mac(mac_bytes: bytes) -> str:
    return ":".join(f"{b:02x}" for b in mac_bytes)


def get_node_id(mac_bytes: bytes) -> str:
    clean = mac_bytes.hex().upper()
    return f"Node-{clean[-4:]}"


# ---------------------------------------------------------------------------
# Persistent send socket (reused across all send_frame calls)
# ---------------------------------------------------------------------------
def _get_send_socket() -> socket.socket:
    """Returns a persistent raw send socket, creating it on first use."""
    global _send_sock
    if _send_sock is None:
        _send_sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
        _send_sock.bind((INTERFACE, 0))
    return _send_sock


def build_ethernet_frame(src_mac: bytes, dst_mac: bytes, payload_dict: dict) -> bytes:
    """
    Constructs a pure Layer-2 Ethernet frame:
    [ Destination MAC (6B) | Source MAC (6B) | EtherType (2B) | Payload ]
    Pads to minimum 60 bytes to satisfy Ethernet minimum frame size.
    """
    header = dst_mac + src_mac + struct.pack("!H", ETH_P_MYCELIUM)
    payload_bytes = json.dumps(payload_dict).encode("utf-8")

    if len(payload_bytes) > MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload too large ({len(payload_bytes)} bytes). "
            f"Maximum is {MAX_PAYLOAD_SIZE} bytes (Ethernet MTU)."
        )

    frame = header + payload_bytes

    # Pad to minimum Ethernet frame size
    if len(frame) < MIN_FRAME_SIZE:
        frame += b"\x00" * (MIN_FRAME_SIZE - len(frame))

    return frame


def send_frame(message: str, msg_type: str = "CHAT", target_mac: bytes = BROADCAST_MAC):
    """Broadcasts a raw Layer-2 frame directly over the air (zero IP)."""
    my_mac = get_my_mac()
    my_id = get_my_node_id()

    payload = {
        "node_id": my_id,
        "mac": format_mac(my_mac),
        "type": msg_type,
        "message": message,
        "timestamp": time.time(),
    }

    raw_frame = build_ethernet_frame(src_mac=my_mac, dst_mac=target_mac, payload_dict=payload)

    sock = _get_send_socket()
    sock.send(raw_frame)

    print(f"[{my_id}] ➔ Broadcasted Layer-2 frame ({len(raw_frame)} bytes): '{message}'")


def listen_forever(on_packet_received=None):
    """Continuously sniffs the air on wlan0 for Mycelium Layer-2 frames."""
    my_mac = get_my_mac()
    my_id = get_my_node_id()

    print("==========================================")
    print(f" Mycelium Pure Layer-2 Listener Active ({my_id})")
    print(f" Hardware MAC: {format_mac(my_mac)} on {INTERFACE}")
    print(f" EtherType   : 0x{ETH_P_MYCELIUM:04X} (Zero IP)")
    print(" Waiting for over-the-air packets...")
    print("==========================================")

    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_MYCELIUM))
    sock.bind((INTERFACE, 0))

    try:
        while True:
            packet, addr = sock.recvfrom(2048)
            if len(packet) < 14:
                continue

            dst_mac = packet[0:6]
            src_mac = packet[6:12]
            ethertype = struct.unpack("!H", packet[12:14])[0]

            # Ignore packets transmitted by ourselves
            if src_mac == my_mac:
                continue

            if ethertype == ETH_P_MYCELIUM:
                # Strip any padding bytes (null bytes at end of frame)
                raw_payload = packet[14:].rstrip(b"\x00")
                try:
                    data = json.loads(raw_payload.decode("utf-8"))
                    sender = data.get("node_id", format_mac(src_mac))
                    msg = data.get("message", "")
                    m_type = data.get("type", "DATA")
                    is_broadcast = dst_mac == BROADCAST_MAC

                    print(f"\n[RECEIVED OVER-THE-AIR]")
                    print(f"  Sender  : {sender} ({format_mac(src_mac)})")
                    print(f"  Target  : {'BROADCAST' if is_broadcast else format_mac(dst_mac)}")
                    print(f"  Type    : {m_type}")
                    print(f"  Payload : {msg}")

                    if on_packet_received:
                        on_packet_received(data, src_mac)

                except Exception as e:
                    print(f"\n[RAW PACKET] From {format_mac(src_mac)}: {raw_payload} (parse error: {e})")
    except KeyboardInterrupt:
        print("\nListener stopped.")
    finally:
        sock.close()


def start_background_listener(on_packet_received=None):
    """
    Runs the listener in a background daemon thread.
    Note: Requires root privileges or CAP_NET_RAW on the Python binary.
    For Arduino App Lab, run via: sudo setcap cap_net_raw+ep $(readlink -f $(which python3))
    """
    t = threading.Thread(target=listen_forever, args=(on_packet_received,), daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  sudo python3 mesh_test.py listen")
        print("  sudo python3 mesh_test.py send <message>")
        print("  sudo python3 mesh_test.py ping")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "listen":
        listen_forever()
    elif cmd == "send":
        msg = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "Hello from peer!"
        send_frame(msg)
    elif cmd == "ping":
        send_frame("PING HEARTBEAT", msg_type="PING")
    else:
        print(f"Unknown command: {cmd}")
