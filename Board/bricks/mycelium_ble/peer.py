"""Linux peer loop and same-user Unix socket for Mycelium."""

import asyncio
import os
import random
import socket
import sys
from collections import OrderedDict
import json
import tempfile
import time
from pathlib import Path

from protocol import TOTAL_SIZE, TYPE_CHAT, pack_packet, unpack_packet

SOCKET = f"\0mycelium-ble-{os.getuid()}"
MAX_PENDING = 128
FRAME_LIFETIME = 120
MAX_SEEN = 1024
MAX_LINKS = 4


class PeerQueue:
    def __init__(self, node_id, on_receive):
        self.node_id = node_id
        self.on_receive = on_receive
        self.pending = OrderedDict()
        self.seen = OrderedDict()
        matrix_path = os.environ.get("MYCELIUM_MATRIX_FILE")
        self.matrix_file = Path(matrix_path) if matrix_path else None
        
    def publish_chat(self, text):
        if not self.matrix_file or not text:
            return
    
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.matrix_file.parent,
                delete=False,
            ) as output:
                temp_file = Path(output.name)
                json.dump({"id": time.time_ns(), "text": text}, output)
    
            temp_file.chmod(0o644)
            temp_file.replace(self.matrix_file)
        except OSError as error:
            print(f"Matrix update failed: {error}", file=sys.stderr, flush=True)
            if temp_file is not None:
                try:
                    temp_file.unlink(missing_ok=True)
                except OSError:
                    pass

    def expire(self):
        now = time.monotonic()
        for key, (_, _, expires) in list(self.pending.items()):
            if expires <= now:
                del self.pending[key]

    def accept(self, frame, incoming=False, source=None):
        packet = unpack_packet(frame)
        key = (packet["origin"], packet["message_id"])
        if key in self.seen or (incoming and packet["origin"] == self.node_id):
            if source and key in self.pending:
                self.pending[key][1].add(source.upper())
            return
        self.expire()
        if not incoming and len(self.pending) >= MAX_PENDING:
            raise ValueError("Send queue is full")

        self.seen[key] = None
        if len(self.seen) > MAX_SEEN:
            self.seen.popitem(last=False)

        if incoming:
            if packet["target"] in (self.node_id, "*"):
                self.on_receive(frame)
                if packet["type_id"] == TYPE_CHAT:
                    self.publish_chat(packet["raw_payload"].decode("utf-8", errors="replace"))
            if packet["target"] == self.node_id or packet["ttl"] <= 1:
                return
            frame = pack_packet(
                packet["origin"], packet["target"], packet["ttl"] - 1,
                packet["hop_count"] + 1, packet["type_id"], packet["raw_payload"],
                message_id=packet["message_id"],
            )

        if len(self.pending) < MAX_PENDING:
            self.pending[key] = (
                frame, {source.upper()} if source else set(),
                time.monotonic() + FRAME_LIFETIME,
            )


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
            raise RuntimeError("Local Mycelium send queue is full")
    finally:
        writer.close()
        await writer.wait_closed()
    return True


async def run(queue, service, inbox, local_address):
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
            try:
                queue.accept(frame)
            except ValueError:
                writer.write(b"0")
            else:
                writer.write(b"1")
            await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError):
            pass
        finally:
            writer.close()

    links = {}
    neighbors = {}
    retry_at = {}

    def discovered(device, advertisement):
        address = device.address.upper()
        if address != local_address and service in advertisement.service_uuids:
            neighbors[address] = (device, time.monotonic())

    async def maintain_link(device):
        address = device.address.upper()
        try:
            async with BleakClient(device, timeout=8) as client:
                print(f"Mesh link up: {address}", flush=True)
                while client.is_connected:
                    queue.expire()
                    for frame, sent, _ in list(queue.pending.values()):
                        if not client.is_connected:
                            break
                        if address in sent:
                            continue
                        await asyncio.wait_for(
                            client.write_gatt_char(inbox, frame, response=True), timeout=5
                        )
                        sent.add(address)
                        print(f"Mesh write confirmed: {address}", flush=True)
                    await asyncio.sleep(0.15)
        except KeyError as error:
            path = error.args[0] if error.args else None
            if not isinstance(path, str) or not path.startswith("/org/bluez/"):
                raise
            print(f"Mesh link {address}: missing BlueZ object {path}", flush=True)
        except (BleakError, EOFError, OSError, TimeoutError) as error:
            print(f"Mesh link {address}: {type(error).__name__}: {error}", flush=True)

    server = await asyncio.start_unix_server(receive_local, path=SOCKET)
    async with server:
        print("Mycelium mesh ready; local commands can enqueue messages", flush=True)
        try:
            while True:
                try:
                    async with BleakScanner(detection_callback=discovered):
                        while True:
                            await asyncio.sleep(random.uniform(0.35, 0.65))
                            now = time.monotonic()
                            for address, task in list(links.items()):
                                if task.done():
                                    del links[address]
                                    retry_at[address] = now + random.uniform(3, 5)
                                    task.result()
                            for address, (device, seen_at) in list(neighbors.items()):
                                if now - seen_at > 25 and address not in links:
                                    neighbors.pop(address)
                                    retry_at.pop(address, None)
                                    continue
                                if len(links) < MAX_LINKS and address not in links and now >= retry_at.get(address, 0):
                                    links[address] = asyncio.create_task(maintain_link(device))
                                    break
                except (BleakError, EOFError, OSError) as error:
                    print(f"Mesh scanner restarting: {error}", file=sys.stderr, flush=True)
                    await asyncio.sleep(5)
        finally:
            for task in links.values():
                task.cancel()
            await asyncio.gather(*links.values(), return_exceptions=True)
