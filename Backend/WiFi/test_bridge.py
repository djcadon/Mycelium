import asyncio
import json
import time
import unittest
from typing import Any

from bridge import BridgeDatagramProtocol, Peer, WiFiBridge
from node import process_datagram
from wifi_protocol import ProtocolError, decode_datagram, encode_datagram
from websockets.legacy.client import connect
from websockets.legacy.server import serve


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        self.sent.append((data, address))


class FakeNodeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        self.sent.append((data, address))


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


class DatagramQueueProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.received: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue()

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        self.received.put_nowait((data, address))


class WiFiBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bridge = WiFiBridge("127.0.0.1", 45802, 8.0, 10.0)
        self.transport = FakeTransport()
        self.bridge.datagram_transport = self.transport  # type: ignore[assignment]
        self.peer_address = ("127.0.0.1", 45801)
        self.bridge.peers["CamenUnoQ"] = Peer(
            node_id="CamenUnoQ",
            name="Camen",
            hardware="Uno Q",
            address=self.peer_address,
            last_seen=time.time(),
        )
        self.client = FakeWebSocket()
        self.bridge.clients.add(self.client)

    async def asyncTearDown(self) -> None:
        for pending in self.bridge.pending.values():
            if pending.timeout_task:
                pending.timeout_task.cancel()

    async def test_send_correlates_destination_ack_and_echo(self) -> None:
        request = {
            "type": "SEND_MESSAGE",
            "requestId": "request-1",
            "targetId": "CamenUnoQ",
            "payload": "hello over Wi-Fi",
        }
        await self.bridge.handle_websocket_message(self.client, json.dumps(request))

        datagram, destination = self.transport.sent[0]
        self.assertEqual(destination, self.peer_address)
        message = decode_datagram(datagram)
        self.assertEqual(message["type"], "DATA")
        self.assertEqual(message["payload"], "hello over Wi-Fi")
        message_id = message["messageId"]
        events = [json.loads(item) for item in self.client.sent]
        self.assertIn("SEND_ACCEPTED", [event["type"] for event in events])
        self.assertEqual(
            [event["packet"]["status"] for event in events if event["type"] == "PACKET_STATE"],
            ["queued", "forwarding", "awaiting_ack"],
        )

        node_socket = FakeNodeSocket()
        process_datagram(
            node_socket,
            datagram,
            ("127.0.0.1", 45802),
            "127.0.0.1",
            45802,
            "CamenUnoQ",
        )
        self.assertEqual(len(node_socket.sent), 2)
        self.assertTrue(all(address == ("127.0.0.1", 45802) for _, address in node_socket.sent))
        for response, _ in node_socket.sent:
            await self.bridge.handle_datagram(response, self.peer_address)

        events = [json.loads(item) for item in self.client.sent]
        self.assertTrue(any(event["type"] == "DELIVERY_ACK" for event in events))
        self.assertTrue(any(event["type"] == "MESSAGE_ECHO" for event in events))
        self.assertNotIn(message_id, self.bridge.pending)

    async def test_offline_peer_is_rejected_without_udp_send(self) -> None:
        self.bridge.peers["CamenUnoQ"].online = False
        request = {
            "type": "SEND_MESSAGE",
            "requestId": "request-2",
            "targetId": "CamenUnoQ",
            "payload": "hello",
        }
        await self.bridge.handle_websocket_message(self.client, json.dumps(request))
        event = json.loads(self.client.sent[0])

        self.assertEqual(event["type"], "SEND_REJECTED")
        self.assertEqual(event["requestId"], "request-2")
        self.assertEqual(self.transport.sent, [])

    async def test_response_from_wrong_ip_is_ignored(self) -> None:
        request = {
            "type": "SEND_MESSAGE",
            "requestId": "request-3",
            "targetId": "CamenUnoQ",
            "payload": "hello",
        }
        await self.bridge.handle_websocket_message(self.client, json.dumps(request))
        message_id = decode_datagram(self.transport.sent[0][0])["messageId"]
        before = len(self.client.sent)
        ack = encode_datagram({
            "type": "ACK",
            "protocolVersion": 1,
            "nodeId": "CamenUnoQ",
            "messageId": message_id,
            "requestId": "request-3",
            "receivedAt": 1_800_000_000,
        })

        await self.bridge.handle_datagram(ack, ("127.0.0.2", 45801))

        self.assertEqual(len(self.client.sent), before)

    async def test_local_websocket_and_udp_round_trip(self) -> None:
        bridge = WiFiBridge("127.0.0.1", 0, 8.0, 2.0)
        loop = asyncio.get_running_loop()
        bridge_transport, _ = await loop.create_datagram_endpoint(
            lambda: BridgeDatagramProtocol(bridge),
            local_addr=("127.0.0.1", 0),
        )
        bridge_address = bridge_transport.get_extra_info("sockname")
        node_protocol = DatagramQueueProtocol()
        node_transport, _ = await loop.create_datagram_endpoint(
            lambda: node_protocol,
            local_addr=("127.0.0.1", 0),
        )
        try:
            async with serve(bridge.websocket_handler, "127.0.0.1", 0) as server:
                websocket_port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{websocket_port}") as websocket:
                    snapshot = json.loads(await asyncio.wait_for(websocket.recv(), 1))
                    self.assertEqual(snapshot["type"], "STATE_SNAPSHOT")

                    node_transport.sendto(encode_datagram({
                        "type": "HELLO",
                        "protocolVersion": 1,
                        "nodeId": "LocalNode",
                        "name": "Local Node",
                        "hardware": "Uno Q",
                    }), bridge_address)
                    await self._receive_event(websocket, "TOPOLOGY_UPDATE")

                    await websocket.send(json.dumps({
                        "type": "SEND_MESSAGE",
                        "requestId": "local-request",
                        "targetId": "LocalNode",
                        "payload": "localhost round trip",
                    }))
                    accepted = await self._receive_event(websocket, "SEND_ACCEPTED")
                    data, source = await asyncio.wait_for(node_protocol.received.get(), 1)
                    outgoing = decode_datagram(data)
                    self.assertEqual(source, bridge_address)
                    self.assertEqual(outgoing["payload"], "localhost round trip")

                    node_transport.sendto(encode_datagram({
                        "type": "ACK",
                        "protocolVersion": 1,
                        "nodeId": "LocalNode",
                        "messageId": accepted["messageId"],
                        "requestId": "local-request",
                        "response": "received",
                    }), bridge_address)
                    node_transport.sendto(encode_datagram({
                        "type": "ECHO",
                        "protocolVersion": 1,
                        "nodeId": "LocalNode",
                        "messageId": accepted["messageId"],
                        "requestId": "local-request",
                        "payload": "localhost round trip",
                    }), bridge_address)
                    await self._receive_event(websocket, "DELIVERY_ACK")
                    await self._receive_event(websocket, "MESSAGE_ECHO")
        finally:
            bridge_transport.close()
            node_transport.close()
            for pending in bridge.pending.values():
                if pending.timeout_task:
                    pending.timeout_task.cancel()

    async def _receive_event(self, websocket: Any, event_type: str) -> dict[str, Any]:
        while True:
            event = json.loads(await asyncio.wait_for(websocket.recv(), 1))
            if event["type"] == event_type:
                return event


class WiFiProtocolTests(unittest.TestCase):
    def test_round_trip_json_datagram(self) -> None:
        message = {"type": "HELLO", "nodeId": "CamenUnoQ", "name": "Camen"}
        self.assertEqual(decode_datagram(encode_datagram(message)), message)

    def test_rejects_datagram_over_limit(self) -> None:
        with self.assertRaises(ProtocolError):
            encode_datagram({"payload": "x" * 1300})

    def test_node_ignores_datagrams_from_unconfigured_bridge(self) -> None:
        node_socket = FakeNodeSocket()
        process_datagram(
            node_socket,
            encode_datagram({
                "type": "DATA",
                "protocolVersion": 1,
                "targetId": "CamenUnoQ",
                "messageId": "message-1",
                "requestId": "request-1",
                "payload": "hello",
            }),
            ("127.0.0.2", 45802),
            "127.0.0.1",
            45802,
            "CamenUnoQ",
        )
        self.assertEqual(node_socket.sent, [])


if __name__ == "__main__":
    unittest.main()
