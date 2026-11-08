#!/usr/bin/env python3

import argparse
import ipaddress
import socket
import sys

DEFAULT_PORT = 45800
MAX_DATAGRAM_BYTES = 1200


def ipv4_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an IPv4 address") from exc

    if not isinstance(address, ipaddress.IPv4Address):
        raise argparse.ArgumentTypeError("must be an IPv4 address")
    if (
        address.is_unspecified
        or address.is_multicast
        or address == ipaddress.IPv4Address("255.255.255.255")
    ):
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test UDP echo between Mycelium Wi-Fi nodes."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    listen = commands.add_parser("listen", help="listen for and echo one peer's messages")
    listen.add_argument(
        "--bind",
        required=True,
        type=ipv4_address,
        help="this node's Wi-Fi IPv4 address (does not bind to all interfaces)",
    )
    listen.add_argument(
        "--allow-peer",
        required=True,
        type=ipv4_address,
        help="only echo datagrams from this peer IPv4 address",
    )
    listen.add_argument("--port", type=port_number, default=DEFAULT_PORT)

    send = commands.add_parser("send", help="send a message and wait for its echo")
    send.add_argument("host", type=ipv4_address, help="listener's Wi-Fi IPv4 address")
    send.add_argument("--port", type=port_number, default=DEFAULT_PORT)
    send.add_argument("--timeout", type=float, default=3.0)
    send.add_argument("--message", required=True)

    return parser


def listen(bind_address: str, allow_peer: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((bind_address, port))
        print(
            f"Listening on {bind_address}:{port}; "
            f"echoing only peer {allow_peer}. Press Ctrl+C to stop."
        )
        try:
            while True:
                data, peer = server.recvfrom(MAX_DATAGRAM_BYTES + 1)
                if peer[0] != allow_peer:
                    print(f"Ignored datagram from unlisted peer {peer[0]}")
                    continue
                if len(data) > MAX_DATAGRAM_BYTES:
                    print(f"Ignored oversized datagram from {peer[0]}")
                    continue

                message = data.decode("utf-8", errors="replace")
                print(f"Received from {peer[0]}:{peer[1]}: {message!r}")
                server.sendto(data, peer)
        except KeyboardInterrupt:
            print("\nStopped.")


def send(host: str, port: int, timeout: float, message: str) -> int:
    if timeout <= 0:
        print("Timeout must be greater than zero.", file=sys.stderr)
        return 2

    payload = message.encode("utf-8")
    if not payload:
        print("Message must not be empty.", file=sys.stderr)
        return 2
    if len(payload) > MAX_DATAGRAM_BYTES:
        print(
            f"Message exceeds the {MAX_DATAGRAM_BYTES}-byte limit.",
            file=sys.stderr,
        )
        return 2

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(timeout)
        client.connect((host, port))
        client.send(payload)
        try:
            response = client.recv(MAX_DATAGRAM_BYTES + 1)
        except TimeoutError:
            print(
                f"No echo from {host}:{port} within {timeout:g} seconds.",
                file=sys.stderr,
            )
            return 1

    if response != payload:
        print("Received a response that did not match the sent message.", file=sys.stderr)
        return 1

    print(f"Echo acknowledged by {host}:{port}: {response.decode('utf-8')}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "listen":
        listen(args.bind, args.allow_peer, args.port)
        return 0
    return send(args.host, args.port, args.timeout, args.message)


if __name__ == "__main__":
    raise SystemExit(main())
