#!/usr/bin/env python3

import argparse
import ipaddress
import socket
import sys
import time
from typing import Any

from wifi_protocol import (
    BRIDGE_UDP_PORT,
    MAX_DATAGRAM_BYTES,
    NODE_UDP_PORT,
    PROTOCOL_VERSION,
    ProtocolError,
    decode_datagram,
    encode_datagram,
    require_payload,
    require_string,
)


def ipv4_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an IPv4 address") from exc
    if not isinstance(address, ipaddress.IPv4Address):
        raise argparse.ArgumentTypeError("must be an IPv4 address")
    if address.is_unspecified or address.is_multicast:
        raise argparse.ArgumentTypeError("must be a unicast IPv4 address")
    return str(address)


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer port") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return port


def node_id(value: str) -> str:
    if not value or len(value.encode("utf-8")) > 64:
        raise argparse.ArgumentTypeError("node ID must be 1-64 UTF-8 bytes")
    return value


def process_datagram(
    sock: socket.socket,
    data: bytes,
    source: tuple[str, int],
    bridge_host: str,
    bridge_port: int,
    own_id: str,
) -> None:
    if source != (bridge_host, bridge_port):
        print(f"Ignored datagram from unconfigured bridge {source[0]}")
        return
    try:
        message = decode_datagram(data)
        if message.get("protocolVersion") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported protocol version")
        if message.get("type") != "DATA":
            raise ProtocolError("unsupported message type")
        target_id = require_string(message, "targetId")
        if target_id != own_id:
            raise ProtocolError(f"message targets {target_id}, not {own_id}")
        message_id = require_string(message, "messageId")
        request_id = require_string(message, "requestId")
        payload = require_payload(message)
    except ProtocolError as exc:
        print(f"Ignored invalid bridge message: {exc}")
        return

    received_at = time.time()
    print(f"Received message {message_id} from bridge: {payload!r}")
    acknowledgement: dict[str, Any] = {
        "type": "ACK",
        "protocolVersion": PROTOCOL_VERSION,
        "nodeId": own_id,
        "messageId": message_id,
        "requestId": request_id,
        "response": f"Acknowledged by {own_id}",
        "receivedAt": received_at,
    }
    echo: dict[str, Any] = {
        "type": "ECHO",
        "protocolVersion": PROTOCOL_VERSION,
        "nodeId": own_id,
        "messageId": message_id,
        "requestId": request_id,
        "payload": payload,
        "receivedAt": time.time(),
    }
    try:
        sock.sendto(encode_datagram(acknowledgement), source)
        sock.sendto(encode_datagram(echo), source)
    except (OSError, ProtocolError) as exc:
        print(f"Could not send message response: {exc}", file=sys.stderr)


def run_node(
    own_id: str,
    name: str,
    bridge_host: str,
    bridge_port: int,
    listen_port: int,
    heartbeat_interval: float,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("0.0.0.0", listen_port))
        sock.settimeout(0.25)
        print(
            f"Wi-Fi node {name} ({own_id}) listening on UDP {listen_port}; "
            f"bridge {bridge_host}:{bridge_port}. Press Ctrl+C to stop."
        )
        next_heartbeat = 0.0
        try:
            while True:
                now = time.monotonic()
                if now >= next_heartbeat:
                    heartbeat = {
                        "type": "HELLO",
                        "protocolVersion": PROTOCOL_VERSION,
                        "nodeId": own_id,
                        "name": name,
                        "hardware": "Uno Q",
                        "timestamp": time.time(),
                    }
                    sock.sendto(encode_datagram(heartbeat), (bridge_host, bridge_port))
                    next_heartbeat = now + heartbeat_interval

                try:
                    data, source = sock.recvfrom(MAX_DATAGRAM_BYTES + 1)
                except socket.timeout:
                    continue
                process_datagram(
                    sock,
                    data,
                    source,
                    bridge_host,
                    bridge_port,
                    own_id,
                )
        except KeyboardInterrupt:
            print("\nNode stopped.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a Mycelium Wi-Fi node on an UNO Q."
    )
    parser.add_argument("--node-id", required=True, type=node_id)
    parser.add_argument("--name", help="display name; defaults to --node-id")
    parser.add_argument("--bridge-host", required=True, type=ipv4_address)
    parser.add_argument("--bridge-port", type=port_number, default=BRIDGE_UDP_PORT)
    parser.add_argument("--listen-port", type=port_number, default=NODE_UDP_PORT)
    parser.add_argument("--heartbeat-interval", type=float, default=2.0)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.heartbeat_interval <= 0:
        parser.error("--heartbeat-interval must be greater than zero")
    run_node(
        own_id=args.node_id,
        name=args.name or args.node_id,
        bridge_host=args.bridge_host,
        bridge_port=args.bridge_port,
        listen_port=args.listen_port,
        heartbeat_interval=args.heartbeat_interval,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
