"""Windows BLE mesh-node transport for the desktop bridge."""

import asyncio
import logging
import os
import time
import uuid

LOG = logging.getLogger("mycelium.ble_node")

SERVICE_UUID = "9cd10001-8ed5-4e55-932e-5c558747527a"
INBOX_UUID = "9cd10002-8ed5-4e55-932e-5c558747527a"


class _BridgeEventReporter:
    def __init__(self, node):
        self.node = node

    def send_mesh_event(self, event, observer_id):
        occurred_at = event["observed_at"] * 1000
        packet_type = self.node.protocol.TYPE_NAMES[event["packet_type"]]
        if event["event_kind"] == self.node.protocol.EVENT_PACKET_RX:
            self.node.queue_event({
                "event": "PACKET_RX",
                "origin": event["origin"],
                "target": event["target"],
                "message_id": event["message_id"],
                "type": packet_type,
                "ttl": event["ttl"],
                "hops": event["hop_count"],
                "observer": observer_id,
                "message": event["message"],
                "related_message_id": event["related_message_id"],
                "occurredAt": occurred_at,
            })
            return

        packet = {
            "id": f'{event["origin"]}:{event["message_id"]:08X}',
            "origin": event["origin"],
            "target": event["target"],
            "message_id": event["message_id"],
            "currentHop": {
                "source": event["hop_source"],
                "target": observer_id,
                "progress": 0,
            },
            "path": [event["origin"], observer_id],
            "ttl": event["ttl"],
            "hop_count": event["hop_count"],
            "payload": {
                "type": packet_type,
                "message": event["message"],
                "timestamp": occurred_at,
            },
            "status": event["status"],
        }
        self.node.queue_event({"event": "PACKET_STATE", "packet": packet})


class WindowsBleMeshNode:
    def __init__(self, bridge, node_id, peer_queue_type, peer_run, protocol, board_ble):
        self.bridge = bridge
        self.node_id = node_id
        self.peer_queue_type = peer_queue_type
        self.peer_run = peer_run
        self.protocol = protocol
        self.board_ble = board_ble
        self.queue = None
        self.provider = None
        self.is_advertising = False
        self.characteristic = None
        self.event_task = None
        self.peer_task = None
        self.event_queue = asyncio.Queue()
        self.loop = asyncio.get_running_loop()
        self.next_message_id = time.time_ns() & 0xFFFFFFFF

    @classmethod
    def create(cls, bridge, node_id):
        import sys
        from pathlib import Path

        brick_path = (
            Path(__file__).resolve().parents[1]
            / "Board"
            / "bricks"
            / "mycelium_ble"
        )
        if str(brick_path) not in sys.path:
            sys.path.insert(0, str(brick_path))

        import ble as board_ble
        import protocol
        from peer import PeerQueue, run

        return cls(bridge, node_id, PeerQueue, run, protocol, board_ble)

    async def start(self):
        if os.name != "nt":
            raise RuntimeError("The laptop BLE mesh node currently requires Windows")

        from winrt.windows.devices.bluetooth import BluetoothAdapter

        adapter = await BluetoothAdapter.get_default_async()
        if adapter is None:
            raise RuntimeError("No Windows Bluetooth adapter is available")
        local_address = self._format_address(adapter.bluetooth_address)

        self.queue = self.peer_queue_type(self.node_id, self._receive_frame)
        self.queue.set_gateway_node_id(self.node_id)
        self.queue.on_event = self.queue_event
        self.bridge.local_node_id = self.node_id

        self.board_ble.MY_NODE_ID = self.node_id
        self.board_ble.BRIDGE_REPORTER = _BridgeEventReporter(self)
        self.board_ble.EVENT_REPORT_REASSEMBLER = self.protocol.EventReportReassembler()

        self.event_task = asyncio.create_task(self._dispatch_events())
        await self._start_gatt_server()
        self.peer_task = asyncio.create_task(
            self.peer_run(
                self.queue,
                SERVICE_UUID,
                INBOX_UUID,
                local_address,
                local_server=False,
            )
        )
        LOG.info("Laptop BLE mesh node %s started at %s", self.node_id, local_address)

    def enqueue_chat(self, target_id, message):
        from peer import canonical_node_id

        target_id = canonical_node_id(target_id)
        self.next_message_id = (self.next_message_id + 1) & 0xFFFFFFFF
        message_id = self.next_message_id
        frame = self.protocol.pack_packet(
            self.node_id,
            target_id,
            ttl=4,
            msg_type=self.protocol.TYPE_CHAT,
            payload=message,
            message_id=message_id,
        )
        self.queue.accept(frame)
        return message_id

    @staticmethod
    def _format_address(address):
        digits = f"{address:012X}"
        return ":".join(digits[index:index + 2] for index in range(0, 12, 2))

    def queue_event(self, event):
        self.event_queue.put_nowait(event)

    async def _dispatch_events(self):
        while True:
            event = await self.event_queue.get()
            try:
                await self.bridge.handle_device_event(event)
            finally:
                self.event_queue.task_done()

    def _receive_frame(self, frame):
        return self.board_ble.process_received_frame(
            frame,
            deduplicate=False,
            event_callback=self.queue_event,
        )

    async def _start_gatt_server(self):
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattCharacteristicProperties,
            GattLocalCharacteristicParameters,
            GattServiceProvider,
            GattServiceProviderAdvertisingParameters,
        )
        from winrt.windows.devices.bluetooth import BluetoothError

        result = await GattServiceProvider.create_async(uuid.UUID(SERVICE_UUID))
        if result.error != BluetoothError.SUCCESS or result.service_provider is None:
            raise RuntimeError(f"Could not create laptop BLE service: {result.error}")
        self.provider = result.service_provider

        parameters = GattLocalCharacteristicParameters()
        parameters.characteristic_properties = GattCharacteristicProperties.WRITE
        char_result = await self.provider.service.create_characteristic_async(
            uuid.UUID(INBOX_UUID),
            parameters,
        )
        if (
            char_result.error != BluetoothError.SUCCESS
            or char_result.characteristic is None
        ):
            raise RuntimeError(f"Could not create laptop BLE inbox: {char_result.error}")
        self.characteristic = char_result.characteristic
        self.characteristic.add_write_requested(self._on_write_requested)

        advertising = GattServiceProviderAdvertisingParameters()
        advertising.is_connectable = True
        advertising.is_discoverable = True
        self.provider.start_advertising_with_parameters(advertising)
        self.is_advertising = True

    def _on_write_requested(self, _sender, args):
        deferral = args.get_deferral()
        try:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._handle_write(args, deferral))
            )
        except RuntimeError:
            deferral.complete()
            raise

    async def _handle_write(self, args, deferral):
        request = None
        try:
            request = await args.get_request_async()
            if request.offset != 0:
                request.respond_with_protocol_error(0x07)
                return

            from winrt.windows.storage.streams import DataReader

            reader = DataReader.from_buffer(request.value)
            value = bytearray(reader.unconsumed_buffer_length)
            reader.read_bytes(value)
            if len(value) != self.protocol.TOTAL_SIZE:
                request.respond_with_protocol_error(0x0D)
                return

            source = self._format_address(args.session.device.bluetooth_address)
            self.queue.accept(bytes(value), incoming=True, source=source)
            request.respond()
        except (OSError, RuntimeError, ValueError) as error:
            LOG.warning("Rejected BLE write to laptop mesh node: %s", error)
            if request is not None:
                request.respond_with_protocol_error(0x80)
        finally:
            deferral.complete()

    async def close(self):
        if self.provider is not None and self.is_advertising:
            self.provider.stop_advertising()
            self.is_advertising = False
        for task in (self.peer_task, self.event_task):
            if task is not None:
                task.cancel()
        tasks = [task for task in (self.peer_task, self.event_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.board_ble.BRIDGE_REPORTER is not None:
            self.board_ble.BRIDGE_REPORTER = None
            self.board_ble.EVENT_REPORT_REASSEMBLER = None
