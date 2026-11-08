#!/usr/bin/env python3

import argparse
import asyncio
import ipaddress
import json
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.legacy.server import serve

from wifi_protocol import (
    BRIDGE_UDP_PORT,
    MAX_DATAGRAM_BYTES,
    MAX_PAYLOAD_BYTES,
    NODE_UDP_PORT,
    PROTOCOL_VERSION,
    ProtocolError,
    decode_datagram,
    encode_datagram,
    require_payload,
    require_string,
)

WS_HOST = "127.0.0.1"
WS_PORT = 8765
HOST_NODE_ID = "Laptop-Base"
ONLINE_TIMEOUT_SECONDS = 8.0
ACK_TIMEOUT_SECONDS = 8.0
MAX_MESSAGE_HISTORY = 100


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


def timestamp() -> float:
    return time.time()


@dataclass
class Peer:
    node_id: str
    name: str
    hardware: str
    address: tuple[str, int]
    last_seen: float
    online: bool = True


@dataclass
class PendingMessage:
    request_id: str
    message_id: str
    target_id: str
    payload: str
    packet: dict[str, Any]
    acknowledged: bool = False
    timeout_task: asyncio.Task[None] | None = None


class BridgeDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, bridge: "WiFiBridge") -> None:
        self.bridge = bridge

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.bridge.datagram_transport = transport

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        asyncio.create_task(self.bridge.handle_datagram(data, address))

    def error_received(self, error: Exception) -> None:
        print(f"UDP transport error: {error}")


class WiFiBridge:
    def __init__(
        self,
        udp_bind: str,
        udp_port: int,
        online_timeout: float,
        ack_timeout: float,
    ) -> None:
        self.udp_bind = udp_bind
        self.udp_port = udp_port
        self.online_timeout = online_timeout
        self.ack_timeout = ack_timeout
        self.datagram_transport: asyncio.BaseTransport | None = None
        self.clients: set[Any] = set()
        self.peers: dict[str, Peer] = {}
        self.pending: dict[str, PendingMessage] = {}
        self.packet_history: list[dict[str, Any]] = []

    def host_node(self) -> dict[str, Any]:
        return {
            "id": HOST_NODE_ID,
            "name": "Wi-Fi Bridge",
            "hardware": "Host Controller",
            "ip": self.udp_bind,
            "status": "online",
            "lastSeen": timestamp(),
            "neighborCount": sum(peer.online for peer in self.peers.values()),
        }

    def peer_node(self, peer: Peer) -> dict[str, Any]:
        return {
            "id": peer.node_id,
            "name": peer.name,
            "hardware": peer.hardware,
            "ip": peer.address[0],
            "status": "online" if peer.online else "offline",
            "lastSeen": peer.last_seen,
            "neighborCount": 1 if peer.online else 0,
        }

    def nodes(self) -> list[dict[str, Any]]:
        return [self.host_node(), *(self.peer_node(peer) for peer in self.peers.values())]

    def links(self) -> list[dict[str, Any]]:
        return [
            {
                "id": f"{HOST_NODE_ID}-{peer.node_id}",
                "source": HOST_NODE_ID,
                "target": peer.node_id,
                "active": peer.online,
            }
            for peer in self.peers.values()
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "STATE_SNAPSHOT",
            "nodes": self.nodes(),
            "links": self.links(),
            "alerts": [],
            "packets": [
                message.packet
                for message in self.pending.values()
            ][-MAX_MESSAGE_HISTORY:],
        }

    async def broadcast(self, event: dict[str, Any]) -> None:
        encoded = json.dumps(event, ensure_ascii=False, allow_nan=False)
        for client in tuple(self.clients):
            try:
                await client.send(encoded)
            except ConnectionClosed:
                self.clients.discard(client)

    async def send_rejected(
        self,
        client: Any,
        request_id: str | None,
        reason: str,
    ) -> None:
        if request_id:
            await client.send(json.dumps({
                "type": "SEND_REJECTED",
                "requestId": request_id,
                "reason": reason,
            }))

    async def websocket_handler(self, websocket: Any, _path: str | None = None) -> None:
        self.clients.add(websocket)
        try:
            await websocket.send(json.dumps(self.snapshot(), allow_nan=False))
            async for raw_message in websocket:
                await self.handle_websocket_message(websocket, raw_message)
        except ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)

    async def handle_websocket_message(self, client: Any, raw_message: Any) -> None:
        request_id: str | None = None
        try:
            if not isinstance(raw_message, (str, bytes)):
                raise ProtocolError("request must be JSON text")
            request = json.loads(raw_message)
            if not isinstance(request, dict):
                raise ProtocolError("request must be a JSON object")
            request_id = request.get("requestId")
            if not isinstance(request_id, str) or not request_id:
                request_id = None
            if request.get("type") != "SEND_MESSAGE":
                raise ProtocolError("unsupported request type")
            request_id = require_string(request, "requestId")
            target_id = require_string(request, "targetId")
            payload = require_payload(request)
        except (json.JSONDecodeError, UnicodeDecodeError, ProtocolError) as exc:
            await self.send_rejected(client, request_id, str(exc))
            return

        peer = self.peers.get(target_id)
        if peer is None or not peer.online:
            await self.send_rejected(client, request_id, f"Peer {target_id} is offline.")
            return
        if timestamp() - peer.last_seen > self.online_timeout:
            peer.online = False
            await self.broadcast({"type": "PEER_UPDATE", "node": self.peer_node(peer)})
            await self.broadcast({"type": "TOPOLOGY_UPDATE", "nodes": self.nodes(), "links": self.links()})
            await self.send_rejected(client, request_id, f"Peer {target_id} is offline.")
            return
        if self.datagram_transport is None:
            await self.send_rejected(client, request_id, "Wi-Fi UDP transport is not ready.")
            return

        message_id = uuid.uuid4().hex
        packet: dict[str, Any] = {
            "id": message_id,
            "requestId": request_id,
            "origin": HOST_NODE_ID,
            "target": target_id,
            "currentHop": {
                "source": HOST_NODE_ID,
                "target": target_id,
                "progress": 0.0,
            },
            "path": [HOST_NODE_ID, target_id],
            "ttl": 1,
            "status": "queued",
            "payload": {
                "type": "DATA",
                "message": payload,
                "timestamp": timestamp(),
            },
        }
        message = {
            "type": "DATA",
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "messageId": message_id,
            "targetId": target_id,
            "payload": payload,
        }
        try:
            datagram = encode_datagram(message)
        except ProtocolError as exc:
            await self.send_rejected(client, request_id, str(exc))
            return

        pending = PendingMessage(
            request_id=request_id,
            message_id=message_id,
            target_id=target_id,
            payload=payload,
            packet=packet,
        )
        self.pending[message_id] = pending
        try:
            self.datagram_transport.sendto(datagram, peer.address)
        except (OSError, RuntimeError) as exc:
            self.pending.pop(message_id, None)
            await self.send_rejected(client, request_id, f"UDP send failed: {exc}")
            return

        await self.broadcast({
            "type": "SEND_ACCEPTED",
            "requestId": request_id,
            "messageId": message_id,
        })
        await self.broadcast({"type": "PACKET_STATE", "packet": packet})
        packet["status"] = "forwarding"
        packet["currentHop"]["progress"] = 1.0
        await self.broadcast({"type": "PACKET_STATE", "packet": packet})
        packet["status"] = "awaiting_ack"
        await self.broadcast({"type": "PACKET_STATE", "packet": packet})
        pending.timeout_task = asyncio.create_task(self.message_timeout(message_id))

    async def handle_datagram(self, data: bytes, address: tuple[str, int]) -> None:
        try:
            message = decode_datagram(data)
            message_type = require_string(message, "type")
            if message_type == "HELLO":
                await self.handle_hello(message, address)
            elif message_type in ("ACK", "ECHO"):
                await self.handle_node_response(message, address, message_type)
            else:
                raise ProtocolError(f"unsupported datagram type: {message_type}")
        except ProtocolError as exc:
            print(f"Ignored invalid datagram from {address[0]}: {exc}")

    async def handle_hello(
        self,
        message: dict[str, Any],
        address: tuple[str, int],
    ) -> None:
        if message.get("protocolVersion") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported protocol version")
        node_id = require_string(message, "nodeId")
        if node_id == HOST_NODE_ID:
            raise ProtocolError(f"node ID {HOST_NODE_ID} is reserved")
        name = message.get("name")
        hardware = message.get("hardware")
        if not isinstance(name, str) or not name:
            raise ProtocolError("name must be a non-empty string")
        if not isinstance(hardware, str) or not hardware:
            raise ProtocolError("hardware must be a non-empty string")

        previous = self.peers.get(node_id)
        topology_changed = (
            previous is None
            or not previous.online
            or previous.address != address
        )
        peer = Peer(
            node_id=node_id,
            name=name,
            hardware=hardware,
            address=address,
            last_seen=timestamp(),
        )
        self.peers[node_id] = peer
        await self.broadcast({"type": "PEER_UPDATE", "node": self.peer_node(peer)})
        if topology_changed:
            await self.broadcast({
                "type": "TOPOLOGY_UPDATE",
                "nodes": self.nodes(),
                "links": self.links(),
            })

    async def handle_node_response(
        self,
        message: dict[str, Any],
        address: tuple[str, int],
        message_type: str,
    ) -> None:
        if message.get("protocolVersion") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported protocol version")
        node_id = require_string(message, "nodeId")
        message_id = require_string(message, "messageId")
        peer = self.peers.get(node_id)
        pending = self.pending.get(message_id)
        if peer is None or peer.address != address:
            raise ProtocolError("response source does not match registered peer")
        if pending is None or pending.target_id != node_id:
            raise ProtocolError("response does not match a pending destination")

        peer.last_seen = timestamp()
        peer.online = True
        if message_type == "ACK":
            if not pending.acknowledged:
                pending.acknowledged = True
                await self.broadcast({
                    "type": "DELIVERY_ACK",
                    "messageId": message_id,
                    "nodeId": node_id,
                    "response": message.get("response"),
                    "receivedAt": timestamp(),
                })
            return

        payload = require_payload(message)
        if payload != pending.payload:
            raise ProtocolError("echo payload does not match the sent message")
        await self.broadcast({
            "type": "MESSAGE_ECHO",
            "messageId": message_id,
            "nodeId": node_id,
            "payload": payload,
            "receivedAt": timestamp(),
        })
        self.pending.pop(message_id, None)
        if pending.timeout_task:
            pending.timeout_task.cancel()

    async def message_timeout(self, message_id: str) -> None:
        await asyncio.sleep(self.ack_timeout)
        pending = self.pending.pop(message_id, None)
        if pending is None or pending.acknowledged:
            return
        packet = dict(pending.packet)
        packet["status"] = "failed"
        packet["reason"] = "No destination acknowledgement before timeout."
        await self.broadcast({"type": "PACKET_STATE", "packet": packet})

    async def expire_peers(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = timestamp()
            changed = False
            for peer in self.peers.values():
                if peer.online and now - peer.last_seen > self.online_timeout:
                    peer.online = False
                    changed = True
                    await self.broadcast({
                        "type": "PEER_UPDATE",
                        "node": self.peer_node(peer),
                    })
            if changed:
                await self.broadcast({
                    "type": "TOPOLOGY_UPDATE",
                    "nodes": self.nodes(),
                    "links": self.links(),
                })

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: BridgeDatagramProtocol(self),
            local_addr=(self.udp_bind, self.udp_port),
            family=socket.AF_INET,
        )
        self.datagram_transport = udp_transport
        expiry_task = asyncio.create_task(self.expire_peers())
        try:
            async with serve(
                self.websocket_handler,
                WS_HOST,
                WS_PORT,
                max_size=MAX_DATAGRAM_BYTES * 2,
            ):
                print(f"UDP peer listener: {self.udp_bind}:{self.udp_port}")
                print(f"Local UI WebSocket: ws://{WS_HOST}:{WS_PORT}")
                await asyncio.Future()
        finally:
            expiry_task.cancel()
            for pending in self.pending.values():
                if pending.timeout_task:
                    pending.timeout_task.cancel()
            udp_transport.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local WebSocket and Wi-Fi UDP bridge."
    )
    parser.add_argument(
        "--udp-bind",
        required=True,
        type=ipv4_address,
        help="the host's Wi-Fi IPv4 address; do not use 0.0.0.0",
    )
    parser.add_argument("--udp-port", type=port_number, default=BRIDGE_UDP_PORT)
    parser.add_argument("--online-timeout", type=float, default=ONLINE_TIMEOUT_SECONDS)
    parser.add_argument("--ack-timeout", type=float, default=ACK_TIMEOUT_SECONDS)
    return parser


async def async_main(args: argparse.Namespace) -> None:
    if args.online_timeout <= 0 or args.ack_timeout <= 0:
        raise ValueError("timeouts must be greater than zero")
    bridge = WiFiBridge(
        udp_bind=args.udp_bind,
        udp_port=args.udp_port,
        online_timeout=args.online_timeout,
        ack_timeout=args.ack_timeout,
    )
    await bridge.run()


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nWi-Fi bridge stopped.")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
