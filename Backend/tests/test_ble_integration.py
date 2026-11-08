import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path

if not hasattr(os, "getuid"):
    os.getuid = os.getpid

REPO_ROOT = Path(__file__).resolve().parents[2]
BRICK_DIR = REPO_ROOT / "Board" / "bricks" / "mycelium_ble"
sys.path.insert(0, str(BRICK_DIR))

from Backend.bridge import (
    BleUiBridge,
    MAX_PAYLOAD_BYTES,
    PEER_LEASE_SECONDS,
)
import ble
import protocol
from peer import PeerQueue
from protocol import (
    EVENT_PACKET_STATE,
    TYPE_ACK,
    TYPE_CHAT,
    TYPE_EVENT_ACK,
    TYPE_EVENT_REPORT,
    TYPE_LINK_REPORT,
    TYPE_ROUTE_ADVERTISEMENT,
    TYPE_SOS,
    TYPE_SOS_CLEAR,
    TOTAL_SIZE,
    EventReportReassembler,
    pack_event_ack_payload,
    pack_event_report_fragments,
    pack_mesh_event_record,
    pack_link_report_payload,
    pack_packet,
    pack_route_advertisement_payload,
    pack_sos_clear_payload,
    unpack_event_ack_payload,
    unpack_link_report_payload,
    unpack_mesh_event_record,
    unpack_packet,
    unpack_route_advertisement_payload,
    unpack_sos_clear_payload,
)


class RecordingBridge(BleUiBridge):
    def __init__(self):
        super().__init__()
        self.events = []

    async def broadcast(self, event):
        self.events.append(event)


class FakeWebSocket:
    def __init__(self):
        self.events = []

    async def send(self, message):
        self.events.append(message)


class FakeMeshNode:
    def __init__(self):
        self.sent = []

    def enqueue_chat(self, target_id, payload):
        message_id = len(self.sent) + 1
        self.sent.append((target_id, payload, message_id))
        return message_id


class BleIntegrationTests(unittest.TestCase):
    def test_direct_link_count_matches_visible_neighbors_not_scan_count(self):
        bridge = RecordingBridge()

        async def scenario():
            announce = {"event": "NODE_ANNOUNCE", "node_id": "Node-0000", "neighborCount": 4}
            await bridge.handle_device_event(announce)
            self.assertEqual(bridge.nodes["Node-0000"]["neighborCount"], 0)
            for source, target in [("Node-0000", "Node-6EBB"), ("Node-6EBB", "Node-0000")]:
                await bridge.handle_device_event({"event": "LINK_UPDATE", "source": source, "target": target})
            await bridge.handle_device_event({**announce, "neighborCount": 7})
            self.assertEqual(bridge.nodes["Node-0000"]["neighborCount"], 1)
            self.assertEqual(bridge.nodes["Node-6EBB"]["neighborCount"], 1)
            for link in bridge.links.values():
                link["active"] = False
            await bridge.handle_device_event(announce)
            self.assertEqual(bridge.nodes["Node-0000"]["neighborCount"], 0)

        asyncio.run(scenario())

    def test_windows_incoming_write_resolves_session_device_id(self):
        from Backend.windows_ble_node import WindowsBleMeshNode
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock, patch

        frame = pack_packet(
            "Node-6EBB", "*", ttl=1, msg_type=TYPE_ROUTE_ADVERTISEMENT,
            payload=pack_route_advertisement_payload([("Node-6EBB", 0)]),
            message_id=456,
        )
        events = []
        queue = PeerQueue("Node-0000", lambda value: None, events.append)
        device = SimpleNamespace(bluetooth_address=0xAABBCCDDEEFF, close=Mock())
        bluetooth = SimpleNamespace(BluetoothLEDevice=SimpleNamespace(
            from_id_async=AsyncMock(return_value=device)))
        reader = SimpleNamespace(
            unconsumed_buffer_length=len(frame),
            read_bytes=lambda target: target.__setitem__(slice(None), frame))
        streams = SimpleNamespace(DataReader=SimpleNamespace(from_buffer=lambda value: reader))
        request = SimpleNamespace(offset=0, value=object(), respond=Mock(),
                                  respond_with_protocol_error=Mock())
        args = SimpleNamespace(
            get_request_async=AsyncMock(return_value=request),
            session=SimpleNamespace(device_id=SimpleNamespace(id="peer-device-id")))
        deferral = SimpleNamespace(complete=Mock())
        node = SimpleNamespace(protocol=protocol, queue=queue,
                               _format_address=WindowsBleMeshNode._format_address)
        with patch.dict(sys.modules, {
            "winrt.windows.devices.bluetooth": bluetooth,
            "winrt.windows.storage.streams": streams,
        }):
            asyncio.run(WindowsBleMeshNode._handle_write(node, args, deferral))
        bluetooth.BluetoothLEDevice.from_id_async.assert_awaited_once_with("peer-device-id")
        self.assertEqual(queue.peer_ids["AA:BB:CC:DD:EE:FF"], "Node-6EBB")
        self.assertTrue(any(event["event"] == "ROUTE_TABLE" for event in events))
        request.respond.assert_called_once()
        request.respond_with_protocol_error.assert_not_called()
        device.close.assert_called_once()
        deferral.complete.assert_called_once()

    def test_route_advertisement_makes_bb_visible_without_scan_link_event(self):
        events = []
        queue = PeerQueue("Node-0000", lambda frame: None, events.append)
        frame = pack_packet(
            "Node-6EBB", "*", ttl=1,
            msg_type=TYPE_ROUTE_ADVERTISEMENT,
            payload=pack_route_advertisement_payload([("Node-6EBB", 0)]),
            message_id=123,
        )
        queue.accept(frame, incoming=True, source="AA:BB:CC:DD:EE:FF")
        bridge = RecordingBridge()
        bridge.local_node_id = "Node-0000"
        for event in events:
            asyncio.run(bridge.handle_device_event(event))
        self.assertEqual(bridge.nodes["Node-6EBB"]["status"], "online")
        self.assertFalse(bridge.links)  # A route does not establish a measured link.
        self.assertTrue(all(isinstance(r["updatedAt"], int) for r in bridge.routes))
        self.assertTrue(any(e["type"] == "TOPOLOGY_UPDATE" for e in bridge.events))
        events.clear()
        queue.last_route_report -= 6
        queue.expire_routes()
        self.assertTrue(any(e["event"] == "ROUTE_TABLE" for e in events))

    def test_windows_write_deferral_is_acquired_before_scheduling(self):
        from Backend.windows_ble_node import WindowsBleMeshNode
        from types import SimpleNamespace
        calls = []
        args = SimpleNamespace(get_deferral=lambda: calls.append("deferral"))
        node = SimpleNamespace(loop=SimpleNamespace(
            call_soon_threadsafe=lambda callback: calls.append("scheduled")))
        WindowsBleMeshNode._on_write_requested(node, None, args)
        self.assertEqual(calls, ["deferral", "scheduled"])

    def test_brick_entrypoint_uses_local_modules(self):
        self.assertEqual(Path(ble.__file__).resolve().parent, BRICK_DIR.resolve())
        self.assertEqual(ble.SERVICE, "9cd10001-8ed5-4e55-932e-5c558747527a")

    def test_wire_ack_preserves_message_id_in_twenty_byte_frame(self):
        frame = pack_packet(
            "Node-0002",
            "Node-0001",
            msg_type=TYPE_ACK,
            message_id=0x1234ABCD,
        )

        packet = unpack_packet(frame)
        self.assertEqual(len(frame), TOTAL_SIZE)
        self.assertEqual(packet["type_id"], TYPE_ACK)
        self.assertEqual(packet["message_id"], 0x1234ABCD)

    def test_gateway_identity_is_distinct_from_broadcast(self):
        frame = pack_packet(
            "Node-0000",
            "Node-0001",
            msg_type=TYPE_CHAT,
            payload="hello",
            message_id=1,
        )
        broadcast = pack_packet("Node-0001", "*", message_id=2)

        self.assertEqual(unpack_packet(frame)["origin"], "Node-0000")
        self.assertEqual(unpack_packet(frame)["target"], "Node-0001")
        self.assertEqual(unpack_packet(broadcast)["target"], "*")
        self.assertEqual(broadcast[2:4], b"\xFF\xFF")
        with self.assertRaisesRegex(ValueError, "Node-FFFF is reserved"):
            pack_packet("Node-FFFF", "Node-0001")
        self.assertEqual(PeerQueue("Node-FFFE", lambda frame: None).node_id, "Node-FFFE")
        with self.assertRaisesRegex(ValueError, "broadcast ID"):
            unpack_route_advertisement_payload(b"\x01\x01\xFF\xFF\x00")

    def test_sos_event_report_is_acknowledged_after_gateway_handoff(self):
        sender = PeerQueue("Node-0001", lambda frame: None)
        sender.set_gateway_node_id("Node-0000")
        relay = PeerQueue("Node-0002", lambda frame: None)
        received = []
        reassembler = EventReportReassembler()

        def receive_event_report(frame):
            packet = unpack_packet(frame)
            _, event, complete = reassembler.add(
                packet["origin"], packet["raw_payload"]
            )
            if event is not None:
                received.append(event)
            return complete

        gateway = PeerQueue("Node-0000", receive_event_report)
        record = pack_mesh_event_record(
            EVENT_PACKET_STATE,
            "Node-0001",
            "Node-0000",
            0x1234,
            TYPE_SOS,
            4,
            0,
            int(time.time()),
            "SOS",
            status="queued",
        )

        self.assertTrue(sender.queue_event_report(record, reliable=True))
        report_frames = [
            pending[0] for pending in sender.pending.values()
            if unpack_packet(pending[0])["type_id"] == TYPE_EVENT_REPORT
        ]
        self.assertGreater(len(report_frames), 1)

        for index, frame in enumerate(report_frames):
            self.assertTrue(relay.accept(
                frame, incoming=True, source="AA:AA:AA:AA:AA:01"
            ))
            forwarded = relay.pending[("Node-0001", unpack_packet(frame)["message_id"])][0]
            self.assertTrue(gateway.accept(
                forwarded, incoming=True, source="AA:AA:AA:AA:AA:02"
            ))
            if index < len(report_frames) - 1:
                self.assertFalse(any(
                    unpack_packet(pending[0])["type_id"] == TYPE_EVENT_ACK
                    for pending in gateway.pending.values()
                ))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["origin"], "Node-0001")
        self.assertEqual(received[0]["status"], "queued")
        ack = next(
            pending[0] for pending in gateway.pending.values()
            if unpack_packet(pending[0])["type_id"] == TYPE_EVENT_ACK
        )
        self.assertEqual(unpack_event_ack_payload(unpack_packet(ack)["raw_payload"]), next(iter(sender.pending_event_reports)))
        self.assertTrue(relay.accept(ack, incoming=True, source="AA:AA:AA:AA:AA:03"))
        sender.accept(
            relay.pending[("Node-0000", unpack_packet(ack)["message_id"])][0],
            incoming=True,
            source="AA:AA:AA:AA:AA:02",
        )
        self.assertEqual(sender.pending_event_reports, {})

    def test_event_record_and_sos_clear_payload_round_trip(self):
        record = pack_mesh_event_record(
            EVENT_PACKET_STATE,
            "Node-0001",
            "Node-0000",
            17,
            TYPE_SOS_CLEAR,
            4,
            1,
            int(time.time()),
            status="forwarding",
            related_message_id=0x1234,
            hop_source="Node-0002",
        )

        event = unpack_mesh_event_record(record)
        fragments = pack_event_report_fragments(22, record)
        reassembler = EventReportReassembler()
        completed = None
        for fragment in fragments:
            completed = reassembler.add("Node-0001", fragment, now=10)

        self.assertEqual(event["related_message_id"], 0x1234)
        self.assertEqual(event["hop_source"], "Node-0002")
        self.assertGreater(len(fragments), 1)
        self.assertTrue(completed[2])
        self.assertEqual(completed[1]["related_message_id"], 0x1234)
        reassembler.forget_completed("Node-0001", 22)
        self.assertFalse(reassembler.add("Node-0001", fragments[0], now=11)[2])
        self.assertEqual(
            unpack_event_ack_payload(pack_event_ack_payload(22)),
            22,
        )
        self.assertEqual(
            unpack_sos_clear_payload(pack_sos_clear_payload(0x1234)),
            0x1234,
        )

    def test_destination_queues_ack_and_relay_preserves_identity(self):
        events = []
        received = []
        destination = PeerQueue(
            "Node-0002",
            lambda frame: received.append(unpack_packet(frame)),
            events.append,
        )
        request = pack_packet(
            "Node-0001",
            "Node-0002",
            ttl=4,
            msg_type=TYPE_CHAT,
            payload="hello",
            message_id=17,
        )

        self.assertTrue(destination.accept(request, incoming=True, source="AA:BB:CC:DD:EE:01"))
        ack = unpack_packet(destination.pending[("Node-0002", 17)][0])
        self.assertEqual(len(received), 1)
        self.assertEqual(ack["type_id"], TYPE_ACK)
        self.assertEqual(ack["origin"], "Node-0002")
        self.assertEqual(ack["target"], "Node-0001")
        self.assertEqual(ack["message_id"], 17)

        relay = PeerQueue("Node-0003", lambda frame: None, events.append)
        self.assertTrue(relay.accept(request, incoming=True, source="AA:BB:CC:DD:EE:01"))
        forwarded = unpack_packet(relay.pending[("Node-0001", 17)][0])
        self.assertEqual(forwarded["ttl"], 3)
        self.assertEqual(forwarded["hop_count"], 1)
        self.assertEqual(forwarded["message_id"], 17)
        self.assertEqual(events[-1]["event"], "PACKET_STATE")

    def test_link_report_frame_is_compact_and_relayable(self):
        payload = pack_link_report_payload("Node-0002", -61, 1_800_000_000)
        frame = pack_packet(
            "Node-0001",
            "*",
            ttl=4,
            msg_type=TYPE_LINK_REPORT,
            payload=payload,
            message_id=18,
        )
        packet = unpack_packet(frame)
        report = unpack_link_report_payload(packet["raw_payload"])

        self.assertEqual(len(frame), TOTAL_SIZE)
        self.assertEqual(packet["type_id"], TYPE_LINK_REPORT)
        self.assertEqual(report, {
            "neighbor_id": "Node-0002",
            "rssi": -61,
            "observed_at": 1_800_000_000,
            "sample_count": 1,
        })

        relay = PeerQueue("Node-0003", lambda received: None)
        self.assertTrue(relay.accept(frame, incoming=True, source="AA:BB:CC:DD:EE:01"))
        forwarded = unpack_packet(relay.pending[("Node-0001", 18)][0])
        self.assertEqual(forwarded["type_id"], TYPE_LINK_REPORT)
        self.assertEqual(forwarded["ttl"], 3)
        self.assertEqual(forwarded["raw_payload"], payload)

    def test_v2_link_report_carries_peer_computed_quality(self):
        observed_at = int(time.time())
        payload = pack_link_report_payload(
            "Node-0002",
            -62,
            observed_at,
            sample_count=3,
            rssi_mad=2,
            version=2,
        )

        report = unpack_link_report_payload(payload)

        self.assertEqual(report["neighbor_id"], "Node-0002")
        self.assertEqual(report["rssi"], -62)
        self.assertEqual(report["rssi_mad"], 2)
        self.assertEqual(report["sample_count"], 3)
        self.assertEqual(report["observed_at"], observed_at)
        self.assertEqual(report["measurement_window_ms"], 30_000)

    def test_route_advertisement_frame_round_trip(self):
        payload = pack_route_advertisement_payload([
            ("Node-0002", 0),
            ("Node-0003", 2),
        ])
        frame = pack_packet(
            "Node-0001",
            "*",
            ttl=1,
            msg_type=TYPE_ROUTE_ADVERTISEMENT,
            payload=payload,
            message_id=19,
        )

        packet = unpack_packet(frame)

        self.assertEqual(len(frame), TOTAL_SIZE)
        self.assertEqual(packet["type_id"], TYPE_ROUTE_ADVERTISEMENT)
        self.assertEqual(unpack_route_advertisement_payload(packet["raw_payload"]), [
            {"destination_id": "Node-0002", "metric": 0},
            {"destination_id": "Node-0003", "metric": 2},
        ])

    def test_node_id_is_canonicalized_before_initializing_routes(self):
        queue = PeerQueue("node-abcd", lambda frame: None)

        self.assertEqual(queue.node_id, "Node-ABCD")
        self.assertEqual(queue.known_destinations, {"Node-ABCD"})
        self.assertIn("Node-ABCD", queue.routes)
        with self.assertRaises(ValueError):
            PeerQueue("Node- 001", lambda frame: None)

    def test_distributed_routes_converge_and_use_poison_reverse(self):
        address_a = "AA:AA:AA:AA:AA:01"
        address_b = "AA:AA:AA:AA:AA:02"
        address_c = "AA:AA:AA:AA:AA:03"
        node_a = PeerQueue("Node-0001", lambda frame: None)
        node_b = PeerQueue("Node-0002", lambda frame: None)
        node_c = PeerQueue("Node-0003", lambda frame: None)

        def deliver_routes(sender, receiver, sender_address, receiver_address):
            sender.advertise_routes([receiver_address])
            entries = [
                (key, pending)
                for key, pending in sender.pending.items()
                if pending[3] == receiver_address
                and unpack_packet(pending[0])["type_id"] == TYPE_ROUTE_ADVERTISEMENT
            ]
            for key, pending in entries:
                receiver.accept(pending[0], incoming=True, source=sender_address)
                sender.pending.pop(key, None)

        deliver_routes(node_a, node_b, address_a, address_b)
        deliver_routes(node_b, node_c, address_b, address_c)
        deliver_routes(node_c, node_b, address_c, address_b)
        deliver_routes(node_b, node_a, address_b, address_a)

        self.assertEqual(node_a.next_hop_for("Node-0003"), address_b)
        self.assertEqual(node_a.routes["Node-0003"]["hop_count"], 2)
        self.assertEqual(node_b.peer_ids[address_a], "Node-0001")
        self.assertEqual(node_c.next_hop_for("Node-0001"), address_b)
        self.assertEqual(node_c.routes["Node-0001"]["hop_count"], 2)

        node_b.advertise_routes([address_a])
        advertisements = [
            unpack_route_advertisement_payload(unpack_packet(pending[0])["raw_payload"])
            for pending in node_b.pending.values()
            if pending[3] == address_a
            and unpack_packet(pending[0])["type_id"] == TYPE_ROUTE_ADVERTISEMENT
        ]
        advertised = {
            route["destination_id"]: route["metric"]
            for batch in advertisements
            for route in batch
        }
        self.assertEqual(advertised["Node-0001"], 0xFF)
        self.assertEqual(advertised["Node-0003"], 1)

    def test_duplicate_direct_ids_quarantine_both_peers_before_dedupe(self):
        events = []
        local = PeerQueue("Node-0001", lambda frame: None, events.append)
        first_address = "AA:AA:AA:AA:AA:01"
        second_address = "AA:AA:AA:AA:AA:02"
        advertisement = pack_packet(
            "Node-0002",
            "*",
            ttl=1,
            msg_type=TYPE_ROUTE_ADVERTISEMENT,
            payload=pack_route_advertisement_payload([
                ("Node-0002", 0),
                ("Node-0003", 0),
            ]),
            message_id=42,
        )

        self.assertTrue(local.accept(
            advertisement, incoming=True, source=first_address
        ))
        self.assertEqual(local.next_hop_for("Node-0003"), first_address)
        with self.assertLogs("peer", level="ERROR") as logs:
            self.assertFalse(local.accept(
                advertisement, incoming=True, source=second_address
            ))

        self.assertIn("collision", "\n".join(logs.output).lower())
        self.assertEqual(local.colliding_node_ids, {"Node-0002"})
        self.assertEqual(
            local.quarantined_neighbors,
            {first_address, second_address},
        )
        self.assertEqual(local.peer_ids, {})
        self.assertIsNone(local.next_hop_for("Node-0003"))
        self.assertTrue(any(event["event"] == "NODE_ID_COLLISION" for event in events))
        ambiguous_packet = pack_packet(
            "Node-0004",
            "Node-0001",
            payload="ignored",
            message_id=44,
        )
        self.assertFalse(local.accept(
            ambiguous_packet, incoming=True, source=first_address
        ))
        with self.assertRaisesRegex(ValueError, "node ID collision"):
            local.accept(pack_packet(
                "Node-0001",
                "Node-0002",
                payload="not delivered",
            ))

    def test_collision_with_local_id_stops_originating_and_routes(self):
        local = PeerQueue("Node-0001", lambda frame: None)
        address = "AA:AA:AA:AA:AA:01"
        advertisement = pack_packet(
            "Node-0001",
            "*",
            ttl=1,
            msg_type=TYPE_ROUTE_ADVERTISEMENT,
            payload=pack_route_advertisement_payload([("Node-0001", 0)]),
            message_id=43,
        )

        with self.assertLogs("peer", level="ERROR"):
            self.assertFalse(local.accept(
                advertisement, incoming=True, source=address
            ))
        self.assertTrue(local.identity_conflicted)
        self.assertFalse(local.advertise_routes([address]))
        with self.assertRaisesRegex(ValueError, "unique MYCELIUM_NODE_ID"):
            local.accept(pack_packet("Node-0001", "Node-0002", payload="hi"))

    def test_expired_peer_route_is_removed_from_forwarding_table(self):
        source_address = "AA:AA:AA:AA:AA:01"
        local = PeerQueue("Node-0002", lambda frame: None)
        remote = PeerQueue("Node-0001", lambda frame: None)
        remote.advertise_routes([source_address])
        route_frame = next(iter(remote.pending.values()))[0]
        local.accept(route_frame, incoming=True, source=source_address)
        local.neighbor_routes[source_address]["Node-0001"]["updated_at"] = (
            time.monotonic() - 31
        )

        local.expire_routes()

        self.assertIsNone(local.next_hop_for("Node-0001"))
        self.assertNotIn("Node-0001", local.routes)

    def test_unicast_uses_route_next_hop_and_unknown_target_floods(self):
        address_a = "AA:AA:AA:AA:AA:01"
        address_b = "AA:AA:AA:AA:AA:02"
        node_a = PeerQueue("Node-0001", lambda frame: None)
        node_b = PeerQueue("Node-0002", lambda frame: None)
        node_b.advertise_routes([address_a])
        route_frame = next(iter(node_b.pending.values()))[0]
        node_a.accept(route_frame, incoming=True, source=address_b)
        packet = pack_packet("Node-0001", "Node-0002", payload="data", message_id=20)

        self.assertTrue(node_a.should_send_to(packet, address_b, set(), [address_b]))
        self.assertFalse(node_a.should_send_to(packet, "AA:AA:AA:AA:AA:03", set(), [
            address_b,
            "AA:AA:AA:AA:AA:03",
        ]))
        unknown = pack_packet("Node-0001", "Node-0003", payload="data", message_id=21)
        self.assertTrue(node_a.should_send_to(unknown, "AA:AA:AA:AA:AA:03", set(), [
            address_b,
            "AA:AA:AA:AA:AA:03",
        ]))

    def test_bridge_preserves_directional_rssi_observations(self):
        bridge = RecordingBridge()

        async def exercise():
            await bridge.handle_device_event({
                "event": "NODE_ANNOUNCE",
                "node_id": "Node-0001",
                "occurredAt": 1_800_000_000_000,
            })
            await bridge.handle_device_event({
                "event": "LINK_UPDATE",
                "source": "Node-0001",
                "target": "Node-0002",
                "rssi": -61,
                "occurredAt": 1_800_000_000_100,
            })
            await bridge.handle_device_event({
                "event": "LINK_UPDATE",
                "source": "Node-0002",
                "target": "Node-0001",
                "rssi": -67,
                "occurredAt": 1_800_000_000_200,
            })

        asyncio.run(exercise())

        self.assertEqual(set(bridge.links), {"Node-0001->Node-0002", "Node-0002->Node-0001"})
        self.assertEqual(bridge.links["Node-0001->Node-0002"]["rssi"], -61)
        self.assertEqual(bridge.links["Node-0002->Node-0001"]["rssi"], -67)
        self.assertNotIn("positionSource", bridge.nodes["Node-0001"])

    def test_rssi_window_publishes_median_dispersion_and_sample_count(self):
        peer = PeerQueue("Node-0001", lambda frame: None)
        summaries = [
            peer.observe_link_rssi("Node-0002", rssi, now=when)
            for when, rssi in ((10, -60), (15, -64), (20, -62))
        ]

        self.assertEqual(summaries[-1], {
            "rssi": -62,
            "rssi_mad": 2,
            "sample_count": 3,
            "measurement_window_ms": 30_000,
        })

    def test_peer_rssi_window_discards_expired_samples(self):
        peer = PeerQueue("Node-0001", lambda frame: None)
        peer.observe_link_rssi("Node-0002", -60, now=10)

        summary = peer.observe_link_rssi("Node-0002", -52, now=41)

        self.assertEqual(summary["rssi"], -52)
        self.assertIsNone(summary["rssi_mad"])
        self.assertEqual(summary["sample_count"], 1)

    def test_bridge_preserves_peer_computed_rssi_quality(self):
        bridge = RecordingBridge()
        observed_at = int(time.time() * 1000)
        link_id = "Node-0001->Node-0002"

        asyncio.run(bridge.handle_device_event({
            "event": "LINK_UPDATE",
            "source": "Node-0001",
            "target": "Node-0002",
            "rssi": -62,
            "rssiMedian": -62,
            "rssiMad": 2,
            "sampleCount": 3,
            "measurementWindowMs": 30_000,
            "occurredAt": observed_at,
        }))

        self.assertEqual(bridge.links[link_id]["rssi"], -62)
        self.assertEqual(bridge.links[link_id]["rssiMedian"], -62)
        self.assertEqual(bridge.links[link_id]["rssiMad"], 2)
        self.assertEqual(bridge.links[link_id]["sampleCount"], 3)
        self.assertEqual(bridge.links[link_id]["measurementWindowMs"], 30_000)

    def test_unobserved_link_expires_independently_of_peer_presence(self):
        bridge = RecordingBridge()

        async def exercise():
            await bridge.handle_device_event({
                "event": "LINK_UPDATE",
                "source": "Node-0001",
                "target": "Node-0002",
                "rssi": -54,
                "occurredAt": int(time.time() * 1000),
            })
            bridge.link_last_seen["Node-0001->Node-0002"] = (
                time.monotonic() - PEER_LEASE_SECONDS - 1
            )
            await bridge.expire_state()

        asyncio.run(exercise())

        self.assertFalse(bridge.links["Node-0001->Node-0002"]["active"])
        self.assertEqual(bridge.nodes["Node-0001"]["neighborCount"], 0)

    def test_destination_ack_correlates_to_websocket_message(self):
        bridge = RecordingBridge()
        bridge.local_node_id = "Node-0001"
        message_id = "Node-0001:00000011"
        bridge.pending_messages[message_id] = {
            "requestId": "request-1",
            "targetId": "Node-0002",
            "deadline": 1e20,
        }

        async def exercise():
            await bridge.handle_device_event({
                "event": "PACKET_RX",
                "origin": "Node-0003",
                "target": "Node-0001",
                "message_id": 17,
                "ttl": 3,
                "observer": "Node-0001",
                "type": "ACK",
                "message": "",
                "occurredAt": 1_800_000_000_000,
            })
            self.assertIn(message_id, bridge.pending_messages)
            await bridge.handle_device_event({
                "event": "PACKET_RX",
                "origin": "Node-0002",
                "target": "Node-0001",
                "message_id": 17,
                "ttl": 3,
                "observer": "Node-0001",
                "type": "ACK",
                "message": "",
                "occurredAt": 1_800_000_000_000,
            })

        asyncio.run(exercise())

        acknowledgement = next(event for event in bridge.events if event["type"] == "DELIVERY_ACK")
        self.assertEqual(acknowledgement["messageId"], message_id)
        self.assertEqual(acknowledgement["nodeId"], "Node-0002")
        self.assertNotIn(message_id, bridge.pending_messages)

    def test_browser_send_limits_payload_and_per_client_rate(self):
        bridge = RecordingBridge()
        bridge.local_node_id = "Node-0000"
        bridge.mesh_node = FakeMeshNode()
        websocket = FakeWebSocket()
        bridge.nodes["Node-0002"] = {
            "id": "Node-0002",
            "status": "online",
            "lastSeen": int(time.time() * 1000),
        }

        async def exercise():
            await bridge.handle_browser_message(
                websocket,
                '{"type":"SEND_MESSAGE","requestId":"oversize","targetId":"Node-0002","payload":"0123456789"}',
            )
            for index in range(11):
                await bridge.handle_browser_message(
                    websocket,
                    json.dumps({
                        "type": "SEND_MESSAGE",
                        "requestId": f"rate-{index}",
                        "targetId": "Node-0002",
                        "payload": "hello",
                    }),
                )

        asyncio.run(exercise())

        self.assertEqual(len(bridge.mesh_node.sent), 10)
        responses = [json.loads(response) for response in websocket.events]
        self.assertIn("9-byte", responses[0]["reason"])
        self.assertIn("rate limit", responses[-1]["reason"])

    def test_browser_send_queues_directly_on_laptop_mesh_node(self):
        bridge = RecordingBridge()
        bridge.local_node_id = "Node-0000"
        bridge.mesh_node = FakeMeshNode()
        bridge.nodes["Node-0002"] = {"id": "Node-0002", "status": "online"}
        websocket = FakeWebSocket()

        async def exercise():
            await bridge.handle_browser_message(
                websocket,
                '{"type":"SEND_MESSAGE","requestId":"request-1","targetId":"Node-0002","payload":"hello"}',
            )
            await bridge.handle_device_event({
                "event": "PACKET_RX",
                "origin": "Node-0002",
                "target": "Node-0000",
                "message_id": 1,
                "ttl": 3,
                "observer": "Node-0000",
                "type": "ACK",
                "message": "",
                "occurredAt": int(time.time() * 1000),
            })

        asyncio.run(exercise())

        self.assertEqual(bridge.mesh_node.sent, [("Node-0002", "hello", 1)])
        self.assertEqual(json.loads(websocket.events[0]), {
            "type": "SEND_ACCEPTED",
            "requestId": "request-1",
            "messageId": "Node-0000:00000001",
        })
        self.assertTrue(any(event["type"] == "DELIVERY_ACK" for event in bridge.events))
        self.assertFalse(bridge.pending_messages)

    def test_laptop_mesh_node_builds_chat_frame_for_peer_queue(self):
        from Backend.windows_ble_node import WindowsBleMeshNode

        node = WindowsBleMeshNode.__new__(WindowsBleMeshNode)
        node.node_id = "Node-0000"
        node.protocol = protocol
        node.queue = PeerQueue("Node-0000", lambda frame: None)
        node.next_message_id = 0

        message_id = node.enqueue_chat("Node-0002", "hello")

        frame = next(iter(node.queue.pending.values()))[0]
        packet = unpack_packet(frame)
        self.assertEqual(message_id, 1)
        self.assertEqual(len(frame), TOTAL_SIZE)
        self.assertEqual(packet["origin"], "Node-0000")
        self.assertEqual(packet["target"], "Node-0002")
        self.assertEqual(packet["type_id"], TYPE_CHAT)
        self.assertEqual(packet["ttl"], 4)
        self.assertEqual(packet["payload"], "hello")

if __name__ == "__main__":
    unittest.main()
