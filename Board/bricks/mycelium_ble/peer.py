"""BLE peer loop and optional local Unix socket for Mycelium."""

import asyncio
import logging
import os
import random
import socket
import sys
from collections import OrderedDict, deque
import json
from statistics import median
import tempfile
import time
from pathlib import Path

from protocol import (
    TOTAL_SIZE,
    BROADCAST_ID,
    TYPE_ACK,
    TYPE_CHAT,
    TYPE_EVENT_ACK,
    TYPE_EVENT_REPORT,
    TYPE_LINK_REPORT,
    TYPE_TOPOLOGY_REPORT,
    TYPE_ROUTE_ADVERTISEMENT,
    TYPE_SOS_CLEAR,
    TYPE_SOS,
    LINK_REPORT_MAX_SAMPLES,
    LINK_REPORT_WINDOW_MS,
    MAX_PENDING_EVENT_REPORTS,
    pack_link_report_payload,
    pack_event_ack_payload,
    pack_event_report_fragments,
    pack_route_advertisement_payload,
    pack_packet,
    unpack_packet,
    unpack_event_ack_payload,
    unpack_event_report_fragment,
    unpack_sos_clear_payload,
    unpack_route_advertisement_payload,
    unpack_link_report_payload,
)

SOCKET = f"\0mycelium-ble-{getattr(os, 'getuid', os.getpid)()}"
MAX_PENDING = 128
MAX_PENDING_STATE = 96
STATE_FRAME_LIFETIME = 15
STATE_TYPES = {TYPE_LINK_REPORT, TYPE_TOPOLOGY_REPORT, TYPE_ROUTE_ADVERTISEMENT}
FRAME_LIFETIME = 120
MAX_SEEN = 1024
MAX_LINKS = 4
LINK_REPORT_INTERVAL = 5
ROUTE_ADVERTISEMENT_INTERVAL = 5
ROUTE_LEASE_SECONDS = 30
MAX_ROUTE_HOPS = 4
MAX_ROUTE_ENTRIES = 32
UNREACHABLE_METRIC = 0xFF


def canonical_node_id(node_id):
    if (
        not isinstance(node_id, str)
        or len(node_id) != 9
        or node_id[:5].lower() != "node-"
        or any(character not in "0123456789abcdefABCDEF" for character in node_id[5:])
    ):
        raise ValueError("node ID must have the form Node-XXXX")
    try:
        value = int(node_id[5:], 16)
    except ValueError as error:
        raise ValueError("node ID must have the form Node-XXXX") from error
    if value == BROADCAST_ID:
        raise ValueError("Node-FFFF is reserved for broadcast")
    return f"Node-{value:04X}"


LOG = logging.getLogger(__name__)
LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


def configure_logging(level_name=None):
    level_name = (level_name or os.environ.get("MYCELIUM_LOG_LEVEL", "INFO")).upper()
    if level_name not in LOG_LEVELS:
        choices = ", ".join(LOG_LEVELS)
        raise ValueError(f"Invalid MYCELIUM_LOG_LEVEL {level_name!r}; expected one of {choices}")

    logging.basicConfig(
        level=LOG_LEVELS[level_name],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger().setLevel(LOG_LEVELS[level_name])


class PeerQueue:
    def __init__(self, node_id, on_receive, on_event=None):
        self.node_id = canonical_node_id(node_id)
        self.on_receive = on_receive
        self.on_event = on_event or (lambda event: None)
        self.pending = OrderedDict()
        self.pending_event_reports = OrderedDict()
        self.seen = OrderedDict()
        self.peer_ids = {}
        self.last_route_report = float("-inf")
        self.neighbor_routes = {}
        self.colliding_node_ids = set()
        self.quarantined_neighbors = set()
        self.identity_conflicted = False
        self.link_measurements = OrderedDict()
        self.gateway_node_id = None
        self.event_report_sequence = time.time_ns() & 0xFFFF
        self.event_frame_sequence = int(time.time_ns()) & 0xFFFFFFFF
        self.known_destinations = {self.node_id}
        self.routes = {
            self.node_id: {"next_hop": None, "hop_count": 0, "updated_at": time.monotonic()}
        }
        self.route_sequence = int(time.time() * 1000) & 0xFFFFFFFF
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
        for key, pending in list(self.pending.items()):
            if pending[2] <= now:
                del self.pending[key]

    def set_node_id(self, node_id):
        node_id = canonical_node_id(node_id)
        if node_id == self.node_id:
            return
        self.known_destinations.discard(self.node_id)
        self.node_id = node_id
        self.known_destinations.add(node_id)
        self.routes = {
            node_id: {"next_hop": None, "hop_count": 0, "updated_at": time.monotonic()}
        }

    def set_gateway_node_id(self, node_id):
        self.gateway_node_id = canonical_node_id(node_id) if node_id else None

    def queue_event_report(self, record, gateway_id=None, reliable=False):
        gateway_id = gateway_id or self.gateway_node_id
        if not gateway_id:
            return False
        gateway_id = canonical_node_id(gateway_id)
        if gateway_id == self.node_id:
            return False
        if len(self.pending_event_reports) >= MAX_PENDING_EVENT_REPORTS and reliable:
            LOG.error("SOS event report queue is full")
            return False

        report_id = None
        for _ in range(0x10000):
            self.event_report_sequence = (self.event_report_sequence + 1) & 0xFFFF
            if self.event_report_sequence not in self.pending_event_reports:
                report_id = self.event_report_sequence
                break
        if report_id is None:
            LOG.error("Could not allocate an event report ID")
            return False

        report = {
            "record": record,
            "gateway_id": gateway_id,
            "reliable": reliable,
            "attempts": 0,
            "retry_at": time.monotonic(),
        }
        if reliable:
            self.pending_event_reports[report_id] = report
        if self._queue_event_report_attempt(report_id, report):
            return True
        if not reliable:
            return False
        report["retry_at"] = time.monotonic() + 1
        return False

    def _queue_event_report_attempt(self, report_id, report):
        fragments = pack_event_report_fragments(report_id, report["record"])
        self.expire()
        self._evict_state_for(len(fragments))
        if len(self.pending) + len(fragments) > MAX_PENDING:
            return False

        for payload in fragments:
            frame = pack_packet(
                origin=self.node_id,
                target=report["gateway_id"],
                ttl=4,
                msg_type=TYPE_EVENT_REPORT,
                payload=payload,
                message_id=self._next_packet_id(),
            )
            try:
                self.accept(frame, one_shot=True)
            except ValueError:
                return False
        report["attempts"] += 1
        report["retry_at"] = time.monotonic() + 3
        return True

    def retry_event_reports(self, now=None):
        now = time.monotonic() if now is None else now
        for report_id, report in list(self.pending_event_reports.items()):
            if now < report["retry_at"]:
                continue
            self._remove_event_report_frames(report_id)
            if report["attempts"] >= 3:
                LOG.error("SOS report %04X was not acknowledged by %s", report_id, report["gateway_id"])
                self.pending_event_reports.pop(report_id, None)
                continue
            if not self._queue_event_report_attempt(report_id, report):
                report["retry_at"] = now + 1

    def acknowledge_event_report(self, report_id):
        report = self.pending_event_reports.pop(report_id, None)
        if report:
            self._remove_event_report_frames(report_id)
            LOG.info("SOS event report %04X acknowledged by the gateway", report_id)

    def _remove_event_report_frames(self, report_id):
        for key, pending in list(self.pending.items()):
            packet = unpack_packet(pending[0])
            if packet["type_id"] != TYPE_EVENT_REPORT:
                continue
            try:
                if unpack_event_report_fragment(packet["raw_payload"])[0] == report_id:
                    del self.pending[key]
            except ValueError:
                continue

    def _next_packet_id(self):
        for _ in range(0x10000):
            self.event_frame_sequence = (self.event_frame_sequence + 1) & 0xFFFFFFFF
            if (self.node_id, self.event_frame_sequence) not in self.seen:
                return self.event_frame_sequence
        raise ValueError("Could not allocate an event frame ID")

    def observe_link_rssi(self, neighbor_id, rssi, now=None):
        neighbor_id = canonical_node_id(neighbor_id)
        if not isinstance(rssi, int) or isinstance(rssi, bool) or not -126 <= rssi <= 0:
            raise ValueError("RSSI must be an integer from -126 to 0 dBm")

        now = time.monotonic() if now is None else now
        samples = self.link_measurements.pop(
            neighbor_id,
            deque(maxlen=LINK_REPORT_MAX_SAMPLES),
        )
        while samples and now - samples[0][0] > LINK_REPORT_WINDOW_MS / 1000:
            samples.popleft()
        samples.append((now, rssi))
        self.link_measurements[neighbor_id] = samples
        while len(self.link_measurements) > MAX_ROUTE_ENTRIES:
            self.link_measurements.popitem(last=False)

        center = median(value for _, value in samples)
        return {
            "rssi": int(round(center)),
            "rssi_mad": (
                int(round(median(abs(value - center) for _, value in samples)))
                if len(samples) >= 2 else None
            ),
            "sample_count": len(samples),
            "measurement_window_ms": LINK_REPORT_WINDOW_MS,
        }

    @staticmethod
    def _sequence_is_newer(sequence, previous):
        difference = (sequence - previous) & 0xFFFFFFFF
        return 0 < difference < 0x80000000

    def _remember_destination(self, destination):
        if destination in self.colliding_node_ids:
            return
        if destination in self.known_destinations or len(self.known_destinations) < MAX_ROUTE_ENTRIES:
            self.known_destinations.add(destination)
        else:
            LOG.warning("Ignoring route destination beyond the %d-entry limit", MAX_ROUTE_ENTRIES)

    def observe_direct_neighbor(self, address, peer_node_id, now=None):
        try:
            peer_node_id = canonical_node_id(peer_node_id)
        except ValueError:
            return
        if peer_node_id == self.node_id or peer_node_id in self.colliding_node_ids:
            return
        address = address.upper()
        now = time.monotonic() if now is None else now
        self.peer_ids[address] = peer_node_id
        self._remember_destination(peer_node_id)
        neighbor_routes = self.neighbor_routes.setdefault(address, {})
        previous = neighbor_routes.get(peer_node_id)
        if previous is None or previous.get("hop_count") != 1 or now - previous.get("updated_at", 0) > 3:
            neighbor_routes[peer_node_id] = {
                "hop_count": 1,
                "sequence": 0,
                "updated_at": now,
            }
            self._recompute_routes()

    def _quarantine_identity(self, node_id, addresses):
        addresses = {address.upper() for address in addresses if address}
        addresses.update(
            address for address, peer_id in self.peer_ids.items()
            if peer_id == node_id
        )
        is_local_collision = node_id == self.node_id
        newly_quarantined = addresses - self.quarantined_neighbors
        newly_conflicted = node_id not in self.colliding_node_ids
        if not newly_quarantined and not newly_conflicted and not (
            is_local_collision and not self.identity_conflicted
        ):
            return

        self.colliding_node_ids.add(node_id)
        self.quarantined_neighbors.update(addresses)
        self.identity_conflicted = self.identity_conflicted or is_local_collision
        for address in addresses:
            self.peer_ids.pop(address, None)
            self.neighbor_routes.pop(address, None)
        self.link_measurements.pop(node_id, None)
        for routes in self.neighbor_routes.values():
            routes.pop(node_id, None)
        if not is_local_collision:
            self.known_destinations.discard(node_id)

        for key, pending in list(self.pending.items()):
            if pending[3] in addresses or (
                is_local_collision and unpack_packet(pending[0])["origin"] == self.node_id
            ):
                del self.pending[key]
            else:
                pending[1].difference_update(addresses)
        if is_local_collision:
            self.pending_event_reports.clear()

        LOG.error(
            "BLE node ID collision for %s at %s; quarantined ambiguous peers. "
            "Assign a unique MYCELIUM_NODE_ID and restart the affected peers.",
            node_id,
            ", ".join(sorted(addresses)) or "local node",
        )
        self.on_event({
            "event": "NODE_ID_COLLISION",
            "node_id": node_id,
            "addresses": sorted(addresses),
            "local": is_local_collision,
        })
        self._recompute_routes()

    def _check_direct_peer_identity(self, packet, source):
        if (
            not source
            or packet["ttl"] != 1
            or packet["target"] != "*"
            or packet["hop_count"] != 0
        ):
            return
        try:
            unpack_route_advertisement_payload(packet["raw_payload"])
        except ValueError:
            return

        source = source.upper()
        advertiser = packet["origin"]
        if advertiser == "*":
            LOG.warning("Ignoring route advertisement using the broadcast ID as a node ID")
            return
        if source in self.quarantined_neighbors:
            return
        if advertiser == self.node_id or advertiser in self.colliding_node_ids:
            self._quarantine_identity(advertiser, {source})
            return

        existing_addresses = {
            address for address, peer_id in self.peer_ids.items()
            if peer_id == advertiser and address != source
        }
        if existing_addresses:
            self._quarantine_identity(advertiser, existing_addresses | {source})

    def _accept_route_advertisement(self, packet, source):
        if not source or packet["ttl"] != 1 or packet["target"] != "*" or packet["hop_count"] != 0:
            LOG.warning("Ignoring route advertisement without direct-neighbor scope")
            return
        try:
            advertised_routes = unpack_route_advertisement_payload(packet["raw_payload"])
        except ValueError as error:
            LOG.warning("Ignoring invalid route advertisement: %s", error)
            return

        source = source.upper()
        advertiser = packet["origin"]
        if (
            source in self.quarantined_neighbors
            or advertiser in self.colliding_node_ids
            or advertiser == self.node_id
            or advertiser == "*"
        ):
            return
        previous_id = self.peer_ids.get(source)
        if previous_id and previous_id != advertiser:
            self.neighbor_routes.pop(source, None)
        now = time.monotonic()
        self.peer_ids[source] = advertiser
        self.observe_direct_neighbor(source, advertiser, now)
        neighbor_routes = self.neighbor_routes.setdefault(source, {})
        changed = False
        for advertised in advertised_routes:
            destination = advertised["destination_id"]
            if destination == self.node_id or destination in self.colliding_node_ids:
                continue
            if destination not in self.known_destinations and len(self.known_destinations) >= MAX_ROUTE_ENTRIES:
                LOG.warning("Ignoring route destination beyond the %d-entry limit", MAX_ROUTE_ENTRIES)
                continue
            previous = neighbor_routes.get(destination)
            sequence = packet["message_id"]
            if previous and not self._sequence_is_newer(sequence, previous["sequence"]):
                continue

            metric = advertised["metric"]
            local_hops = metric + 1 if metric < MAX_ROUTE_HOPS else UNREACHABLE_METRIC
            neighbor_routes[destination] = {
                "hop_count": local_hops,
                "sequence": sequence,
                "updated_at": now,
            }
            self._remember_destination(destination)
            changed = True

        if changed:
            self._recompute_routes()

    def _recompute_routes(self):
        now = time.monotonic()
        routes = {
            self.node_id: {"next_hop": None, "hop_count": 0, "updated_at": now}
        }
        for neighbor, advertisements in list(self.neighbor_routes.items()):
            for destination, advertisement in list(advertisements.items()):
                if now - advertisement["updated_at"] > ROUTE_LEASE_SECONDS:
                    del advertisements[destination]
                    continue
                hops = advertisement["hop_count"]
                if (
                    destination == self.node_id
                    or destination in self.colliding_node_ids
                    or not 1 <= hops <= MAX_ROUTE_HOPS
                ):
                    continue
                current = routes.get(destination)
                candidate = {"next_hop": neighbor, "hop_count": hops, "updated_at": advertisement["updated_at"]}
                if current is None or (
                    hops,
                    self.peer_ids.get(neighbor, neighbor),
                ) < (
                    current["hop_count"],
                    self.peer_ids.get(current["next_hop"], current["next_hop"]),
                ):
                    routes[destination] = candidate
            if not advertisements:
                del self.neighbor_routes[neighbor]
                self.peer_ids.pop(neighbor, None)

        changed = {
            destination: (route["next_hop"], route["hop_count"])
            for destination, route in self.routes.items()
        } != {
            destination: (route["next_hop"], route["hop_count"])
            for destination, route in routes.items()
        }
        self.routes = routes
        if changed or now - self.last_route_report >= ROUTE_ADVERTISEMENT_INTERVAL:
            self.last_route_report = now
            self.on_event({"event": "ROUTE_TABLE", "routes": self.export_routes()})

    def expire_routes(self):
        self._recompute_routes()

    def advertise_topology(self):
        """Flood confirmed direct adjacency independently of scanner callbacks/RSSI."""
        if self.identity_conflicted:
            return
        now = time.monotonic()
        for address, neighbor in list(self.peer_ids.items()):
            route = self.neighbor_routes.get(address, {}).get(neighbor)
            if (address in self.quarantined_neighbors or route is None
                    or route["hop_count"] != 1
                    or now - route["updated_at"] > ROUTE_LEASE_SECONDS):
                continue
            frame = pack_packet(
                self.node_id, "*", ttl=4, msg_type=TYPE_TOPOLOGY_REPORT,
                payload=int(neighbor[5:], 16).to_bytes(2, "big"),
                message_id=self._next_packet_id(),
            )
            try:
                self.accept(frame)
            except ValueError as error:
                LOG.warning("Could not queue topology report: %s", error)
                break
            self.on_event({"event": "LINK_UPDATE", "source": self.node_id,
                           "target": neighbor, "active": True})

    def next_hop_for(self, destination, excluded=(), available=None):
        if destination in self.colliding_node_ids:
            return None
        now = time.monotonic()
        candidates = []
        excluded = {address.upper() for address in excluded}
        available = None if available is None else {address.upper() for address in available}
        for neighbor, advertisements in self.neighbor_routes.items():
            if (
                neighbor in excluded
                or neighbor in self.quarantined_neighbors
                or (available is not None and neighbor not in available)
            ):
                continue
            advertisement = advertisements.get(destination)
            if (
                advertisement is None
                or advertisement["hop_count"] > MAX_ROUTE_HOPS
                or now - advertisement["updated_at"] > ROUTE_LEASE_SECONDS
            ):
                continue
            candidates.append((
                advertisement["hop_count"],
                self.peer_ids.get(neighbor, neighbor),
                neighbor,
            ))
        return min(candidates)[2] if candidates else None

    def should_send_to(self, frame, address, sent, available=None, send_to=None):
        address = address.upper()
        if address in self.quarantined_neighbors:
            return False
        if send_to is not None and address != send_to.upper():
            return False
        if address in sent:
            return False
        packet = unpack_packet(frame)
        if packet["target"] == "*":
            return True
        next_hop = self.next_hop_for(
            packet["target"],
            excluded=sent,
            available=available,
        )
        return next_hop is None or next_hop == address

    def export_routes(self):
        exported = [{
            "destination": self.node_id,
            "nextHop": None,
            "hopCount": 0,
            "status": "local",
        }]
        for destination, route in sorted(self.routes.items()):
            if destination == self.node_id:
                continue
            next_hop = self.peer_ids.get(route["next_hop"])
            if next_hop:
                exported.append({
                    "destination": destination,
                    "nextHop": next_hop,
                    "hopCount": route["hop_count"],
                    "status": "reachable",
                })
        return exported

    def advertise_routes(self, neighbors):
        if self.identity_conflicted:
            return
        neighbors = {address.upper() for address in neighbors}
        for key, pending in list(self.pending.items()):
            if pending[3] in neighbors and unpack_packet(pending[0])["type_id"] == TYPE_ROUTE_ADVERTISEMENT:
                del self.pending[key]

        destinations = sorted(self.known_destinations | set(self.routes))
        if len(destinations) > MAX_ROUTE_ENTRIES:
            destinations = destinations[:MAX_ROUTE_ENTRIES]
        for neighbor in sorted(neighbors):
            entries = []
            for destination in destinations:
                route = self.routes.get(destination)
                if destination == self.node_id:
                    metric = 0
                elif route is None or route["hop_count"] > MAX_ROUTE_HOPS:
                    metric = UNREACHABLE_METRIC
                elif route["next_hop"] == neighbor:
                    metric = UNREACHABLE_METRIC
                else:
                    metric = route["hop_count"]
                entries.append((destination, metric))

            for offset in range(0, len(entries), 2):
                frame = pack_packet(
                    origin=self.node_id,
                    target="*",
                    ttl=1,
                    msg_type=TYPE_ROUTE_ADVERTISEMENT,
                    payload=pack_route_advertisement_payload(entries[offset:offset + 2]),
                    message_id=self._next_route_sequence(),
                )
                try:
                    self.accept(frame, send_to=neighbor, one_shot=True)
                except ValueError as error:
                    LOG.warning("Could not queue route advertisement for %s: %s", neighbor, error)
                    return

    def _next_route_sequence(self):
        self.route_sequence = (self.route_sequence + 1) & 0xFFFFFFFF
        return self.route_sequence

    def report_packet(self, packet, status, current_hop_source=None, reason=None):
        if packet["type_id"] in {
            TYPE_LINK_REPORT,
            TYPE_TOPOLOGY_REPORT,
            TYPE_ROUTE_ADVERTISEMENT,
            TYPE_EVENT_REPORT,
            TYPE_EVENT_ACK,
        }:
            return
        state = {
            "id": f'{packet["origin"]}:{packet["message_id"]:08X}',
            "origin": packet["origin"],
            "target": packet["target"],
            "message_id": packet["message_id"],
            "currentHop": {
                "source": current_hop_source,
                "target": self.node_id,
                "progress": 0,
            },
            "path": [packet["origin"], self.node_id],
            "ttl": packet["ttl"],
            "hop_count": packet["hop_count"],
            "payload": {
                "type": packet["type"],
                "message": packet["payload"],
                "timestamp": int(time.time() * 1000),
            },
            "status": status,
        }
        if packet["type_id"] == TYPE_SOS_CLEAR:
            try:
                state["related_message_id"] = unpack_sos_clear_payload(packet["raw_payload"])
            except ValueError:
                state["status"] = "failed"
                state["reason"] = "Invalid SOS clear payload"
        if reason:
            state["reason"] = reason
        self.on_event({"event": "PACKET_STATE", "packet": state})

    @staticmethod
    def _state_key(packet, send_to):
        kind = packet["type_id"]
        payload = packet["raw_payload"]
        if kind == TYPE_LINK_REPORT:
            identity = payload[1:3]
        elif kind == TYPE_TOPOLOGY_REPORT:
            identity = payload[:2]
        elif kind == TYPE_ROUTE_ADVERTISEMENT:
            # Preserve separate chunks of a multi-frame route advertisement.
            identity = tuple(r["destination_id"] for r in unpack_route_advertisement_payload(payload))
        else:
            return None
        return packet["origin"], kind, identity, send_to.upper() if send_to else None

    def _evict_state_for(self, slots):
        for key, pending in list(self.pending.items()):
            if len(self.pending) + slots <= MAX_PENDING:
                break
            if unpack_packet(pending[0])["type_id"] in STATE_TYPES:
                del self.pending[key]

    def accept(self, frame, incoming=False, source=None, send_to=None, one_shot=False):
        packet = unpack_packet(frame)
        key = (packet["origin"], packet["message_id"])
        if packet["type_id"] in {TYPE_CHAT, TYPE_ACK}:
            LOG.info("MESH_%s node=%s type=%s id=%s:%08X target=%s source=%s ttl=%s hops=%s queue=%d",
                     "RX" if incoming else "LOCAL", self.node_id, packet["type"],
                     packet["origin"], packet["message_id"], packet["target"], source,
                     packet["ttl"], packet["hop_count"], len(self.pending))
        if incoming and packet["type_id"] == TYPE_ROUTE_ADVERTISEMENT:
            self._check_direct_peer_identity(packet, source)
        if incoming and source and source.upper() in self.quarantined_neighbors:
            LOG.warning("MESH_DROP node=%s origin=%s id=%08X reason=quarantined-source",
                        self.node_id, packet["origin"], packet["message_id"])
            return False
        if incoming and packet["origin"] == "*":
            LOG.warning("Ignoring packet using the broadcast ID as an origin")
            return False
        if incoming and packet["origin"] in self.colliding_node_ids:
            return False
        if not incoming and self.identity_conflicted:
            raise ValueError(
                f"Node ID {self.node_id} conflicts with another BLE peer; set a unique MYCELIUM_NODE_ID"
            )
        if not incoming and packet["target"] in self.colliding_node_ids:
            raise ValueError(
                f"Destination {packet['target']} has a BLE node ID collision"
            )
        if incoming and packet["origin"] == self.node_id:
            if key not in self.seen:
                LOG.error("Ignoring packet from a peer using this node's ID %s", self.node_id)
            return False
        if key in self.seen:
            if packet["type_id"] in {TYPE_CHAT, TYPE_ACK}:
                LOG.info("MESH_DROP node=%s origin=%s id=%08X reason=duplicate",
                         self.node_id, packet["origin"], packet["message_id"])
            if source and key in self.pending:
                self.pending[key][1].add(source.upper())
            return False
        self.expire()
        state_key = self._state_key(packet, send_to)
        if state_key is not None:
            for old_key, pending in list(self.pending.items()):
                previous = unpack_packet(pending[0])
                if self._state_key(previous, pending[3]) == state_key:
                    older = (
                        unpack_link_report_payload(packet["raw_payload"])["observed_at"]
                        < unpack_link_report_payload(previous["raw_payload"])["observed_at"]
                        if packet["type_id"] == TYPE_LINK_REPORT else
                        not self._sequence_is_newer(packet["message_id"], previous["message_id"])
                    )
                    if older:
                        return False
                    del self.pending[old_key]
            state_entries = [k for k, p in self.pending.items()
                             if unpack_packet(p[0])["type_id"] in STATE_TYPES]
            if len(state_entries) >= MAX_PENDING_STATE:
                del self.pending[state_entries[0]]
        else:
            self._evict_state_for(1)
        # Receiving routing control must not depend on outbound queue space.
        if len(self.pending) >= MAX_PENDING and not (incoming and packet["type_id"] == TYPE_ROUTE_ADVERTISEMENT):
            if incoming:
                self.report_packet(packet, "failed", reason="Relay queue is full")
                return False
            raise ValueError("Send queue is full")

        self.seen[key] = None
        if len(self.seen) > MAX_SEEN:
            self.seen.popitem(last=False)

        if incoming and source and packet["hop_count"] == 0 and packet["origin"] != self.node_id and packet["origin"] != "*":
            source_addr = source.upper()
            origin = packet["origin"]
            if source_addr not in self.quarantined_neighbors and origin not in self.colliding_node_ids:
                if self.peer_ids.get(source_addr) != origin:
                    self.peer_ids[source_addr] = origin
                    self.observe_direct_neighbor(source_addr, origin)

        if incoming and packet["type_id"] == TYPE_ROUTE_ADVERTISEMENT:
            self._accept_route_advertisement(packet, source)
            return True
        if (
            incoming
            and packet["type_id"] == TYPE_EVENT_ACK
            and packet["target"] == self.node_id
        ):
            try:
                self.acknowledge_event_report(unpack_event_ack_payload(packet["raw_payload"]))
            except ValueError as error:
                LOG.warning("Ignoring invalid event report ACK: %s", error)
            return True

        if incoming:
            if packet["target"] in (self.node_id, "*"):
                received = False
                if not self.identity_conflicted:
                    received = self.on_receive(frame)
                if (
                    packet["type_id"] == TYPE_EVENT_REPORT
                    and packet["target"] == self.node_id
                    and received
                ):
                    report_id = unpack_event_report_fragment(packet["raw_payload"])[0]
                    ack = pack_packet(
                        self.node_id,
                        packet["origin"],
                        ttl=4,
                        msg_type=TYPE_EVENT_ACK,
                        payload=pack_event_ack_payload(report_id),
                        message_id=self._next_packet_id(),
                    )
                    self.accept(ack)
                if packet["type_id"] == TYPE_CHAT:
                    self.publish_chat(packet["raw_payload"].decode("utf-8", errors="replace"))
                if packet["target"] == self.node_id and packet["type_id"] == TYPE_CHAT:
                    ack = pack_packet(
                        self.node_id,
                        packet["origin"],
                        ttl=4,
                        msg_type=TYPE_ACK,
                        message_id=packet["message_id"],
                    )
                    self.accept(ack)
            if packet["target"] == self.node_id or packet["ttl"] <= 1:
                if packet["type_id"] in {TYPE_CHAT, TYPE_ACK}:
                    LOG.info("MESH_%s node=%s origin=%s id=%08X target=%s",
                             "DELIVERED" if packet["target"] == self.node_id else "TTL_EXPIRED",
                             self.node_id, packet["origin"], packet["message_id"], packet["target"])
                return True
            frame = pack_packet(
                packet["origin"], packet["target"], packet["ttl"] - 1,
                packet["hop_count"] + 1, packet["type_id"], packet["raw_payload"],
                message_id=packet["message_id"],
            )
            forwarded = unpack_packet(frame)
            if packet["type_id"] not in {
                TYPE_LINK_REPORT,
                TYPE_ROUTE_ADVERTISEMENT,
                TYPE_EVENT_REPORT,
                TYPE_EVENT_ACK,
            }:
                self.report_packet(forwarded, "forwarding", current_hop_source=(
                    self.peer_ids.get(source.upper())
                    if source else None
                ))

        self.pending[key] = (
            frame, {source.upper()} if source else set(),
            time.monotonic() + (STATE_FRAME_LIFETIME if state_key is not None else FRAME_LIFETIME), send_to.upper() if send_to else None,
            one_shot,
        )
        if packet["type_id"] in {TYPE_CHAT, TYPE_ACK}:
            LOG.info("MESH_QUEUED node=%s origin=%s id=%08X target=%s next_hop=%s",
                     self.node_id, packet["origin"], packet["message_id"], packet["target"],
                     self.next_hop_for(packet["target"], excluded={source} if source else ()))
        if not incoming and packet["type_id"] not in {
            TYPE_LINK_REPORT,
            TYPE_ROUTE_ADVERTISEMENT,
            TYPE_EVENT_REPORT,
            TYPE_EVENT_ACK,
        }:
            self.report_packet(packet, "queued")
        return True


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


async def run(queue, service, inbox, local_address, local_server=True):
    configure_logging()
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
    consecutive_failures = {}
    last_link_event = {}

    def discovered(device, advertisement):
        address = device.address.upper()
        if address != local_address and service in advertisement.service_uuids:
            now = time.monotonic()
            neighbors[address] = (device, now)
            peer_node_id = queue.peer_ids.get(address)
            if peer_node_id:
                queue.observe_direct_neighbor(address, peer_node_id, now)
            if peer_node_id and now - last_link_event.get(address, 0) >= LINK_REPORT_INTERVAL:
                last_link_event[address] = now
                event = {
                    "event": "LINK_UPDATE",
                    "source": queue.node_id,
                    "target": peer_node_id,
                    "active": True,
                }
                rssi = getattr(advertisement, "rssi", None)
                if isinstance(rssi, int) and not isinstance(rssi, bool) and -126 <= rssi <= 0:
                    quality = queue.observe_link_rssi(peer_node_id, rssi)
                    event.update({
                        "rssi": quality["rssi"],
                        "rssiMedian": quality["rssi"],
                        "sampleCount": quality["sample_count"],
                        "measurementWindowMs": quality["measurement_window_ms"],
                    })
                    if quality["rssi_mad"] is not None:
                        event["rssiMad"] = quality["rssi_mad"]
                    observed_at = int(time.time())
                    report_message_id = time.time_ns() & 0xFFFFFFFF
                    report = pack_packet(
                        origin=queue.node_id,
                        target="*",
                        ttl=4,
                        msg_type=TYPE_LINK_REPORT,
                        payload=pack_link_report_payload(
                            peer_node_id,
                            quality["rssi"],
                            observed_at,
                            sample_count=quality["sample_count"],
                            rssi_mad=quality["rssi_mad"],
                            version=2,
                        ),
                        message_id=report_message_id,
                    )
                    try:
                        if queue.accept(report):
                            event["reportId"] = f"{queue.node_id}:{report_message_id:08X}"
                            event["observedAt"] = observed_at * 1000
                    except ValueError as error:
                        LOG.warning("Could not queue link report for %s: %s", peer_node_id, error)
                queue.on_event(event)

    async def maintain_link(device):
        address = device.address.upper()
        stage = "connect"
        LOG.debug("Connecting to BLE peer %s", address)
        try:
            async with BleakClient(device, timeout=8) as client:
                LOG.info("GATT connected to %s", address)
                stage = "connected"
                while client.is_connected:
                    queue.expire()
                    def priority(item):
                        kind = unpack_packet(item[1][0])["type_id"]
                        return 0 if kind in {TYPE_SOS, TYPE_ACK, TYPE_EVENT_ACK, TYPE_SOS_CLEAR} else 2 if kind in STATE_TYPES else 1
                    for key, pending in sorted(list(queue.pending.items()), key=priority):
                        if queue.pending.get(key) is not pending:
                            continue
                        frame, sent, _, send_to, one_shot = pending
                        if not client.is_connected:
                            break
                        if not queue.should_send_to(
                            frame,
                            address,
                            sent,
                            available=links.keys(),
                            send_to=send_to,
                        ):
                            continue
                        stage = "gatt_write"
                        LOG.debug("Writing %d-byte frame to %s", len(frame), address)
                        await asyncio.wait_for(
                            client.write_gatt_char(inbox, frame, response=True), timeout=5
                        )
                        sent.add(address)
                        if one_shot:
                            queue.pending.pop(key, None)
                        transmitted = unpack_packet(frame)
                        LOG.log(logging.INFO if transmitted["type_id"] in {TYPE_CHAT, TYPE_ACK} else logging.DEBUG,
                                 "GATT write confirmed by %s type=%s origin=%s id=%08X target=%s ttl=%s hops=%s",
                                 address, transmitted["type"], transmitted["origin"],
                                 transmitted["message_id"], transmitted["target"],
                                 transmitted["ttl"], transmitted["hop_count"])
                        stage = "connected"
                    await asyncio.sleep(0.15)
                LOG.debug("GATT connection to %s ended", address)
        except KeyError as error:
            path = error.args[0] if error.args else None
            if not isinstance(path, str) or not path.startswith("/org/bluez/"):
                raise
            LOG.warning("BLE peer %s failed during %s: missing BlueZ object %s", address, stage, path)
        except (BleakError, EOFError, OSError, TimeoutError) as error:
            LOG.warning(
                "BLE peer %s failed during %s: %s (%r)",
                address,
                stage,
                type(error).__name__,
                error,
                exc_info=LOG.isEnabledFor(logging.DEBUG),
            )

    async def scan_loop():
        LOG.info("Mycelium mesh ready; local commands can enqueue messages")
        next_announce = 0.0
        next_route_advertisement = 0.0
        try:
            while True:
                try:
                    async with BleakScanner(detection_callback=discovered):
                        while True:
                            await asyncio.sleep(random.uniform(0.35, 0.65))
                            now = time.monotonic()
                            queue.expire_routes()
                            queue.retry_event_reports(now)
                            if now >= next_announce:
                                if not queue.identity_conflicted:
                                    queue.on_event({
                                        "event": "NODE_ANNOUNCE",
                                        "node_id": queue.node_id,
                                        "neighborCount": len(neighbors),
                                    })
                                next_announce = now + 5
                            if now >= next_route_advertisement:
                                queue.advertise_routes(neighbors.keys())
                                queue.advertise_topology()
                                next_route_advertisement = now + ROUTE_ADVERTISEMENT_INTERVAL
                            for address, task in list(links.items()):
                                if task.done():
                                    del links[address]
                                    failed = False
                                    try:
                                        task.result()
                                    except Exception as error:
                                        failed = True
                                        LOG.warning("BLE link task for %s completed with error: %s", address, error)
                                    if failed:
                                        consecutive_failures[address] = consecutive_failures.get(address, 0) + 1
                                        backoff = min(60.0, 4.0 * (1.8 ** min(consecutive_failures[address], 6))) + random.uniform(1.0, 3.0)
                                        retry_at[address] = now + backoff
                                    else:
                                        consecutive_failures.pop(address, None)
                                        retry_at[address] = now + random.uniform(1.0, 3.0)
                            for address, (device, seen_at) in list(neighbors.items()):
                                if now - seen_at > 25 and address not in links:
                                    neighbors.pop(address)
                                    retry_at.pop(address, None)
                                    consecutive_failures.pop(address, None)
                                    last_link_event.pop(address, None)
                                    continue
                                if len(links) < MAX_LINKS and address not in links and now >= retry_at.get(address, 0):
                                    links[address] = asyncio.create_task(maintain_link(device))
                except (BleakError, EOFError, OSError) as error:
                    LOG.warning(
                        "BLE scanner failed; restarting in 5 seconds: %s (%r)",
                        type(error).__name__,
                        error,
                        exc_info=LOG.isEnabledFor(logging.DEBUG),
                    )
                    await asyncio.sleep(5)
        finally:
            for task in links.values():
                task.cancel()
            await asyncio.gather(*links.values(), return_exceptions=True)

    if local_server:
        server = await asyncio.start_unix_server(receive_local, path=SOCKET)
        async with server:
            await scan_loop()
    else:
        await scan_loop()
