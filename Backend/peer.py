"""Linux peer loop and same-user Unix socket for Mycelium."""

import asyncio
import os
import random
import socket
import sys
from collections import OrderedDict

from protocol import TOTAL_SIZE, pack_packet, unpack_packet

SOCKET = f"\0mycelium-ble-{os.getuid()}"
MAX_PENDING = 128
MAX_SEEN = 512


class PeerQueue:
    def __init__(self, node_id, on_receive):
        self.node_id = node_id
        self.on_receive = on_receive
        self.pending = OrderedDict()
        self.seen = OrderedDict()

    def accept(self, frame, incoming=False):
        packet = unpack_packet(frame)
        # Hop count and TTL change during relay. Keep the existing wire format.
        key = frame[:4] + frame[6:]
        if key in self.seen:
            return
        self.seen[key] = None
        if len(self.seen) > MAX_SEEN:
            self.seen.popitem(last=False)

        if incoming:
            if packet["origin"] == self.node_id:
                return
            self.on_receive(frame)
            if packet["target"] == self.node_id or packet["ttl"] <= 1:
                return
            frame = pack_packet(
                packet["origin"], packet["target"], packet["ttl"] - 1,
                packet["hop_count"] + 1, packet["type_id"], packet["raw_payload"],
            )

        self.pending[key] = (frame, set())
        if len(self.pending) > MAX_PENDING:
            self.pending.popitem(last=False)


async def enqueue(frame):
    """Return False when no local daemon is running."""
    try:
        reader, writer = await asyncio.open_unix_connection(SOCKET)
    except ConnectionRefusedError:
        return False
    try:
        writer.write(frame)
        await writer.drain()
        if await asyncio.wait_for(reader.readexactly(1), 5) != b"1":
            raise RuntimeError("Local Mycelium peer rejected the frame")
    finally:
        writer.close()
        await writer.wait_closed()
    return True


async def run(queue, service, inbox):
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError

    async def receive_local(reader, writer):
        try:
            # Abstract sockets have no filesystem permissions.
            transport_socket = writer.get_extra_info("socket")
            credentials = transport_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            if int.from_bytes(credentials[4:8], sys.byteorder) != os.getuid():
                return
            frame = await asyncio.wait_for(reader.readexactly(TOTAL_SIZE), 5)
            queue.accept(frame)
            writer.write(b"1")
            await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError):
            pass
        finally:
            writer.close()

    async def exchange(device, pending):
        confirmed = 0
        try:
            async with BleakClient(device, timeout=8) as client:
                for frame, sent_to in pending:
                    await asyncio.wait_for(client.write_gatt_char(inbox, frame, response=True), 5)
                    sent_to.add(device.address)
                    confirmed += 1
        except (BleakError, EOFError, OSError, TimeoutError) as error:
            print(f"Peer {device.address}: {confirmed} writes confirmed; {type(error).__name__}: {error}",
                  file=sys.stderr, flush=True)

    server = await asyncio.start_unix_server(receive_local, path=SOCKET)
    async with server:
        print("Mycelium peer ready; local commands can enqueue messages", flush=True)
        while True:
            await asyncio.sleep(random.uniform(0.3, 1.0))
            try:
                peers = await BleakScanner.discover(timeout=3, return_adv=True)
            except (BleakError, EOFError, OSError) as error:
                print(f"BLE scan failed: {error}", file=sys.stderr, flush=True)
                await asyncio.sleep(2)
                continue
            for device, advertisement in peers.values():
                if service not in advertisement.service_uuids:
                    continue
                pending = [item for item in queue.pending.values() if device.address not in item[1]]
                if pending:
                    await exchange(device, pending)
