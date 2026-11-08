#!/usr/bin/env python3
"""Loopback WebSocket bridge for the laptop's BLE mesh node."""

import argparse
import asyncio
import json
import logging
import math
import re
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit

import websockets

WS_HOST = "127.0.0.1"
WS_PORT = 8765
MAX_PAYLOAD_BYTES = 9
PEER_LEASE_SECONDS = 20
ACK_TIMEOUT_SECONDS = 30
MAX_SENDS_PER_MINUTE = 10
SEND_RATE_WINDOW_SECONDS = 60
NODE_ID_PATTERN = re.compile(r"Node-(?!FFFF\Z)[0-9A-Fa-f]{4}\Z", re.IGNORECASE)
LOG = logging.getLogger("mycelium.bridge")


def _wire_message_id(origin: str, message_id: int) -> str:
    return f"{origin}:{message_id:08X}"


def _valid_loopback_host(value: str | None) -> bool:
    if not value:
        return False
    try:
        hostname = urlsplit(f"//{value}").hostname
    except ValueError:
        return False
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _valid_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and _valid_loopback_host(parsed.netloc)


class BleUiBridge:
    def __init__(self):
        self.clients = set()
        self.nodes: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.link_last_seen: dict[str, float] = {}
        self.packets: dict[str, dict] = {}
        self.alerts: dict[str, dict] = {}
        self.routes: list[dict] = []
        self.route_last_seen = 0.0
        self.local_node_id: str | None = None
        self.pending_messages: dict[str, dict] = {}
        self.request_map: dict[str, str] = {}
        self.client_send_times: dict[Any, deque[float]] = {}
        self.mesh_node = None

    async def broadcast(self, event: dict):
        if not self.clients:
            return
        payload = json.dumps(event, separators=(",", ":"))
        failed = []
        for websocket in tuple(self.clients):
            try:
                await websocket.send(payload)
            except websockets.ConnectionClosed:
                failed.append(websocket)
        for websocket in failed:
            self.clients.discard(websocket)

    async def publish_topology(self):
        await self.broadcast({
            "type": "TOPOLOGY_UPDATE",
            "nodes": list(self.nodes.values()),
            "links": list(self.links.values()),
        })

    async def handle_device_event(self, event: dict):
        event_type = event.get("event")
        occurred_at = event.get("occurredAt", int(time.time() * 1000))
        if not isinstance(occurred_at, (int, float)) or not math.isfinite(occurred_at):
            LOG.warning("Ignoring BLE event with invalid occurredAt")
            return
        occurred_at = int(occurred_at)

        if event_type == "NODE_ANNOUNCE":
            node_id = event.get("node_id")
            if not isinstance(node_id, str) or not NODE_ID_PATTERN.fullmatch(node_id):
                LOG.warning("Ignoring node announcement with invalid node ID")
                return
            self.local_node_id = node_id
            node = self._upsert_node(node_id, occurred_at)
            node["hardware"] = "Laptop BLE Bridge"
            node["name"] = node_id
            node["ip"] = "BLE"
            # Scanner sightings are not confirmed topology links.
            self._refresh_neighbor_counts()
            if isinstance(event.get("mac"), str):
                node["mac"] = event["mac"]
            await self.broadcast({"type": "PEER_UPDATE", "node": node})
            await self.publish_topology()
            return

        if event_type == "LINK_UPDATE":
            source = event.get("source")
            target = event.get("target")
            if not all(isinstance(value, str) and NODE_ID_PATTERN.fullmatch(value) for value in (source, target)):
                LOG.warning("Ignoring link update with invalid node IDs")
                return
            active = event.get("active", True)
            if not isinstance(active, bool):
                LOG.warning("Ignoring link update with invalid active flag")
                return
            source_node = self._upsert_node(source, occurred_at)
            target_node = self._upsert_node(target, occurred_at)
            source_node["hardware"] = (
                "Laptop BLE Bridge" if source == self.local_node_id else "Arduino UNO Q"
            )
            target_node["hardware"] = (
                "Laptop BLE Bridge" if target == self.local_node_id else "Arduino UNO Q"
            )
            source_node["status"] = target_node["status"] = "online"
            link_id = f"{source}->{target}"
            existing_link = self.links.get(link_id)
            if existing_link and occurred_at < existing_link.get("observedAt", 0):
                return
            link = {"id": link_id, "source": source, "target": target, "active": active}
            # Adjacency heartbeats carry no new signal measurement.
            if existing_link:
                for field in ("rssi", "rssiMedian", "rssiMad", "sampleCount", "measurementWindowMs"):
                    if field in existing_link:
                        link[field] = existing_link[field]
            rssi = event.get("rssi")
            if (
                isinstance(rssi, (int, float))
                and not isinstance(rssi, bool)
                and math.isfinite(rssi)
            ):
                link["rssi"] = rssi
            for field in ("rssiMedian", "rssiMad", "sampleCount", "measurementWindowMs"):
                value = event.get(field)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                ):
                    link[field] = value
            link["observedAt"] = occurred_at
            self.links[link_id] = link
            self.link_last_seen[link_id] = time.monotonic()
            self._refresh_neighbor_counts()
            await self.publish_topology()
            return

        if event_type == "PACKET_STATE":
            packet = event.get("packet")
            if isinstance(packet, dict):
                packet_id = packet.get("id")
                if isinstance(packet_id, str):
                    self.packets[packet_id] = packet
                    await self.broadcast({"type": "PACKET_STATE", "packet": packet})
            return

        if event_type == "PACKET_RX":
            await self._handle_received_packet(event, occurred_at)
            return

        if event_type == "ROUTE_TABLE":
            routes = event.get("routes")
            if not isinstance(routes, list):
                LOG.warning("Ignoring route table with invalid routes")
                return
            self.routes = [{**route, "updatedAt": occurred_at} for route in routes]
            for route in self.routes:
                if route.get("status") == "reachable":
                    self._upsert_node(route["destination"], occurred_at)
            self.route_last_seen = time.monotonic()
            await self.broadcast({
                "type": "ROUTE_UPDATE",
                "gatewayNodeId": self.local_node_id,
                "routes": self.routes,
                "occurredAt": occurred_at,
            })
            await self.publish_topology()
            return

        if event_type in {"SEND_ACCEPTED", "SEND_REJECTED"}:
            if event_type == "SEND_ACCEPTED":
                request_id = event.get("requestId")
                message_id = event.get("messageId")
                if isinstance(request_id, str) and isinstance(message_id, str):
                    self.request_map[request_id] = message_id
                    target = event.get("targetId")
                    self.pending_messages[message_id] = {
                        "requestId": request_id,
                        "targetId": target if isinstance(target, str) else None,
                        "deadline": time.monotonic() + ACK_TIMEOUT_SECONDS,
                    }
            await self.broadcast({"type": event_type, **{
                key: event[key] for key in ("requestId", "messageId", "reason") if key in event
            }})
            return

        LOG.debug("Ignoring unsupported BLE event: %s", event_type)

    async def _handle_received_packet(self, event: dict, occurred_at: int):
        origin = event.get("origin")
        target = event.get("target")
        message_id = event.get("message_id")
        packet_type = event.get("type")
        observer = event.get("observer", self.local_node_id)
        if (not isinstance(origin, str) or not NODE_ID_PATTERN.fullmatch(origin)
                or not isinstance(target, str)
                or (target != "*" and not NODE_ID_PATTERN.fullmatch(target))
                or not isinstance(message_id, int) or not 0 <= message_id <= 0xFFFFFFFF):
            LOG.warning("Ignoring received packet with invalid identity fields")
            return

        self._upsert_node(origin, occurred_at)
        observer_id = observer if isinstance(observer, str) and NODE_ID_PATTERN.fullmatch(observer) else target
        packet_id = _wire_message_id(origin, message_id)
        packet_state = {
            "id": packet_id,
            "origin": origin,
            "target": target,
            "message_id": message_id,
            "currentHop": {"target": observer_id, "progress": 0},
            "path": [origin, observer_id],
            "ttl": event.get("ttl", 0),
            "hop_count": event.get("hops", 0),
            "status": "forwarding" if observer_id != target else "awaiting_ack",
            "payload": {
                "type": packet_type if packet_type in {
                    "HEARTBEAT", "SOS", "TELEMETRY", "CHAT", "ACK", "SOS_CLEAR"
                } else "DATA",
                "message": event.get("message", ""),
                "timestamp": occurred_at,
            },
        }
        self.packets[packet_id] = packet_state
        await self.broadcast({"type": "PACKET_STATE", "packet": packet_state})

        if packet_type == "SOS":
            alert_id = f"{origin}:{message_id:08X}"
            if alert_id not in self.alerts:
                alert = {
                    "id": alert_id,
                    "nodeId": origin,
                    "kind": "SOS",
                    "message": event.get("message", "SOS"),
                    "occurredAt": occurred_at,
                }
                self.alerts[alert_id] = alert
                await self.broadcast({"type": "ALERT_RAISED", "alert": alert})

        if packet_type == "SOS_CLEAR":
            related_message_id = event.get("related_message_id")
            if (
                isinstance(related_message_id, int)
                and not isinstance(related_message_id, bool)
                and 0 <= related_message_id <= 0xFFFFFFFF
            ):
                alert_id = f"{origin}:{related_message_id:08X}"
                alert = self.alerts.get(alert_id)
                if alert is not None and alert.get("nodeId") == origin:
                    del self.alerts[alert_id]
                    await self.broadcast({
                        "type": "ALERT_CLEARED",
                        "alertId": alert_id,
                        "nodeId": origin,
                        "occurredAt": occurred_at,
                    })

        if packet_type == "ACK" and target == self.local_node_id:
            correlated_message_id = _wire_message_id(target, message_id)
            pending = self.pending_messages.get(correlated_message_id)
            if pending and pending.get("targetId") == origin:
                self.pending_messages.pop(correlated_message_id, None)
                self.request_map.pop(pending["requestId"], None)
                await self.broadcast({
                    "type": "DELIVERY_ACK",
                    "messageId": correlated_message_id,
                    "nodeId": origin,
                    "receivedAt": occurred_at,
                })
                self.packets.pop(correlated_message_id, None)

    def _upsert_node(self, node_id: str, last_seen: int) -> dict:
        node = self.nodes.get(node_id)
        if node is None:
            node = {
                "id": node_id,
                "name": node_id,
                "position": [0, 0, 0],
                "status": "online",
                "hardware": "Arduino UNO Q",
                "ip": "BLE",
                "lastSeen": last_seen,
                "neighborCount": 0,
            }
            self.nodes[node_id] = node
        else:
            node["lastSeen"] = last_seen
            node["status"] = "online"
        return node

    def _refresh_neighbor_counts(self):
        for node in self.nodes.values():
            node["neighborCount"] = len({
                link["target"] if link["source"] == node["id"] else link["source"]
                for link in self.links.values()
                if link["active"]
                and node["id"] in (link["source"], link["target"])
                and link["source"] != link["target"]
            })

    async def handle_browser_message(self, websocket, raw_message: str):
        try:
            request = json.loads(raw_message)
        except json.JSONDecodeError:
            await websocket.send(json.dumps({
                "type": "SEND_REJECTED", "requestId": "invalid", "reason": "Invalid JSON request.",
            }))
            return
        if not isinstance(request, dict) or request.get("type") != "SEND_MESSAGE":
            return

        request_id = request.get("requestId")
        target_id = request.get("targetId")
        payload = request.get("payload")
        if isinstance(target_id, str) and NODE_ID_PATTERN.fullmatch(target_id):
            target_id = f"Node-{target_id[5:].upper()}"
        payload_length = None
        if isinstance(payload, str):
            try:
                payload_length = len(payload.encode("utf-8"))
            except UnicodeEncodeError:
                pass
        reason = None
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            reason = "requestId must contain between 1 and 128 characters."
        elif not isinstance(target_id, str) or not NODE_ID_PATTERN.fullmatch(target_id):
            reason = "Select a valid Node-XXXX destination."
        elif not isinstance(payload, str) or not payload.strip():
            reason = "Message payload cannot be empty."
        elif payload_length is None:
            reason = "Message payload must contain valid UTF-8 text."
        elif payload_length > MAX_PAYLOAD_BYTES:
            reason = f"Message exceeds the {MAX_PAYLOAD_BYTES}-byte BLE payload limit."
        elif target_id == self.local_node_id:
            reason = "Select a remote peer."
        elif target_id not in self.nodes or self.nodes[target_id]["status"] != "online":
            reason = "Destination has not been discovered as a reachable BLE peer."
        elif request_id in self.request_map:
            reason = "requestId has already been used."
        elif len(self.pending_messages) >= 32:
            reason = "The bridge send queue is full; try again shortly."
        elif self.mesh_node is None:
            reason = "The laptop BLE mesh node is unavailable."
        elif self.local_node_id is None:
            reason = "The laptop BLE mesh node has not initialized."

        send_times = self.client_send_times.setdefault(websocket, deque())
        now = time.monotonic()
        while send_times and now - send_times[0] >= SEND_RATE_WINDOW_SECONDS:
            send_times.popleft()
        if reason is None and len(send_times) >= MAX_SENDS_PER_MINUTE:
            reason = "Message rate limit reached; wait before sending again."

        if reason:
            response_request_id = (
                request_id if isinstance(request_id, str) and request_id else "invalid"
            )
            await websocket.send(json.dumps({
                "type": "SEND_REJECTED",
                "requestId": response_request_id,
                "reason": reason,
            }))
            return

        try:
            message_id = self.mesh_node.enqueue_chat(target_id, payload)
        except (ValueError, RuntimeError) as error:
            await websocket.send(json.dumps({
                "type": "SEND_REJECTED",
                "requestId": request_id,
                "reason": str(error),
            }))
            return

        send_times.append(now)
        wire_message_id = _wire_message_id(self.local_node_id, message_id)
        self.pending_messages[wire_message_id] = {
            "requestId": request_id,
            "targetId": target_id,
            "deadline": now + ACK_TIMEOUT_SECONDS,
        }
        self.request_map[request_id] = wire_message_id
        await websocket.send(json.dumps({
            "type": "SEND_ACCEPTED",
            "requestId": request_id,
            "messageId": wire_message_id,
        }))

    async def expire_state(self):
        now = time.monotonic()
        now_ms = int(time.time() * 1000)
        changed = False
        for node in self.nodes.values():
            age = time.time() * 1000 - node["lastSeen"]
            if node["status"] != "offline" and age > PEER_LEASE_SECONDS * 1000:
                node["status"] = "offline"
                changed = True
        for link in self.links.values():
            source = self.nodes.get(link["source"])
            link_id = link["id"]
            link_age = now - self.link_last_seen.get(link_id, 0)
            if link["active"] and (
                source is None
                or source["status"] == "offline"
                or link_age > PEER_LEASE_SECONDS
            ):
                link["active"] = False
                changed = True
        if (
            self.routes
            and now - self.route_last_seen > PEER_LEASE_SECONDS
        ):
            self.routes = []
            await self.broadcast({
                "type": "ROUTE_UPDATE",
                "gatewayNodeId": self.local_node_id,
                "routes": [],
                "occurredAt": now_ms,
            })
        for message_id, pending in list(self.pending_messages.items()):
            if pending["deadline"] <= now:
                self.pending_messages.pop(message_id)
                self.request_map.pop(pending["requestId"], None)
                packet = self.packets.get(message_id)
                if packet:
                    failed = {**packet, "status": "failed", "reason": "Destination ACK timed out."}
                    self.packets[message_id] = failed
                    await self.broadcast({"type": "PACKET_STATE", "packet": failed})
        if changed:
            self._refresh_neighbor_counts()
            await self.publish_topology()

    async def websocket_handler(self, websocket):
        headers = getattr(websocket, "request_headers", None)
        if headers is None:
            headers = getattr(getattr(websocket, "request", None), "headers", {})
        host = headers.get("Host")
        origin = headers.get("Origin")
        if not _valid_loopback_host(host) or (
            origin is not None and not _valid_origin(origin)
        ):
            await websocket.close(code=1008, reason="Loopback browser clients only")
            return

        self.clients.add(websocket)
        await websocket.send(json.dumps({
            "type": "BRIDGE_CAPABILITIES",
            "transport": "BLE",
            "maxPayloadBytes": MAX_PAYLOAD_BYTES,
        }))
        await websocket.send(json.dumps({
            "type": "STATE_SNAPSHOT",
            "nodes": list(self.nodes.values()),
            "links": list(self.links.values()),
            "routes": list(self.routes),
            "alerts": list(self.alerts.values()),
            "packets": list(self.packets.values()),
        }))
        try:
            async for raw_message in websocket:
                await self.handle_browser_message(websocket, raw_message)
        finally:
            self.clients.discard(websocket)
            self.client_send_times.pop(websocket, None)


async def run(node_id: str = "Node-0000"):
    bridge = BleUiBridge()
    if __package__:
        from .windows_ble_node import WindowsBleMeshNode
    else:
        from windows_ble_node import WindowsBleMeshNode

    mesh_node = WindowsBleMeshNode.create(bridge, node_id)
    bridge.mesh_node = mesh_node
    try:
        await mesh_node.start()
        async with websockets.serve(bridge.websocket_handler, WS_HOST, WS_PORT):
            while True:
                await asyncio.sleep(1)
                for task, description in (
                    (mesh_node.peer_task, "BLE peer loop"),
                    (mesh_node.event_task, "BLE event dispatcher"),
                ):
                    if task is not None and task.done():
                        task.result()
                        raise RuntimeError(f"The {description} stopped unexpectedly")
                await bridge.expire_state()
    finally:
        await mesh_node.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--node-id",
        default="Node-0000",
        help="reserved mesh node identity for the laptop BLE bridge",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not NODE_ID_PATTERN.fullmatch(args.node_id):
        parser.error("--node-id must be a Node-XXXX ID other than Node-FFFF")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run(args.node_id.upper()))
    except KeyboardInterrupt:
        LOG.info("Bridge stopped")


if __name__ == "__main__":
    main()
