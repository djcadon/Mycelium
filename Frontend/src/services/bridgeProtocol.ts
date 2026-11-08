import type { MeshAlert, MeshLink, MeshNode, HopPacket } from '../types/mesh';

export const MAX_PAYLOAD_BYTES = 512;

export type BridgeEvent =
  | { type: 'STATE_SNAPSHOT'; nodes: MeshNode[]; links: MeshLink[]; alerts: MeshAlert[]; packets: HopPacket[] }
  | { type: 'TOPOLOGY_UPDATE'; nodes: MeshNode[]; links: MeshLink[] }
  | { type: 'PEER_UPDATE'; node: MeshNode }
  | { type: 'ALERT_RAISED'; alert: MeshAlert }
  | { type: 'ALERT_CLEARED'; alertId: string; occurredAt: number }
  | { type: 'PACKET_STATE'; packet: HopPacket }
  | { type: 'SEND_ACCEPTED'; requestId: string; messageId: string }
  | { type: 'SEND_REJECTED'; requestId: string; reason: string }
  | { type: 'DELIVERY_ACK'; messageId: string; nodeId: string; response?: string; receivedAt: number }
  | { type: 'MESSAGE_ECHO'; messageId: string; nodeId: string; payload: string; receivedAt: number };

export interface SendMessageRequest {
  type: 'SEND_MESSAGE';
  requestId: string;
  targetId: string;
  payload: string;
}

type JsonRecord = Record<string, unknown>;

function record(value: unknown, label: string): JsonRecord {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as JsonRecord;
}

function string(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function number(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite number`);
  }
  return value;
}

function timestamp(value: unknown, label: string): number {
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    if (!Number.isFinite(parsed)) throw new Error(`${label} must be a valid timestamp`);
    return parsed;
  }
  const parsed = number(value, label);
  return parsed < 1_000_000_000_000 ? parsed * 1000 : parsed;
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
}

function parseNode(value: unknown): MeshNode {
  const input = record(value, 'node');
  const id = string(input.id, 'node.id');
  const status = input.status;
  if (!['online', 'offline', 'transmitting', 'alert'].includes(String(status))) {
    throw new Error(`node ${id} has an unsupported status`);
  }

  const positionInput = input.position;
  let position: [number, number, number] = [0, 0, 0];
  let positionSource: MeshNode['positionSource'] = 'diagrammatic';
  let positionDimensions: 2 | 3 = 2;
  let positionConfidence: number | undefined;
  let positionUnits: string | undefined;

  if (typeof positionInput === 'object' && positionInput !== null && !Array.isArray(positionInput)) {
    const positionRecord = record(positionInput, `node ${id}.position`);
    const coordinates = array(positionRecord.coordinates, `node ${id}.position.coordinates`);
    const source = positionRecord.source;
    if (coordinates.length !== 2 && coordinates.length !== 3) {
      throw new Error(`node ${id} position must have 2 or 3 coordinates`);
    }
    if (source !== 'measured' && source !== 'estimated') {
      throw new Error(`node ${id} position source must be measured or estimated`);
    }
    const values = coordinates.map((value, index) => number(value, `node ${id}.position[${index}]`));
    positionDimensions = values.length as 2 | 3;
    position = values.length === 2
      ? [values[0], 0, values[1]]
      : [values[0], values[1], values[2]];
    positionSource = source;
    if (positionRecord.confidence !== undefined) {
      positionConfidence = number(positionRecord.confidence, `node ${id}.position.confidence`);
      if (positionConfidence < 0 || positionConfidence > 1) {
        throw new Error(`node ${id} position confidence must be between 0 and 1`);
      }
    }
    if (positionRecord.units !== undefined) {
      positionUnits = string(positionRecord.units, `node ${id}.position.units`);
    }
  }

  const sensorsInput = input.sensors;
  let sensors: MeshNode['sensors'];
  if (sensorsInput !== undefined) {
    const sensorRecord = record(sensorsInput, `node ${id}.sensors`);
    if (sensorRecord.sosPressed !== undefined && typeof sensorRecord.sosPressed !== 'boolean') {
      throw new Error(`node ${id}.sensors.sosPressed must be a boolean`);
    }
    sensors = {
      temperature: sensorRecord.temperature === undefined
        ? undefined
        : number(sensorRecord.temperature, `node ${id}.sensors.temperature`),
      sosPressed: sensorRecord.sosPressed,
      vibration: sensorRecord.vibration === undefined
        ? undefined
        : number(sensorRecord.vibration, `node ${id}.sensors.vibration`),
    };
  }

  return {
    id,
    name: typeof input.name === 'string' ? input.name : id,
    position,
    positionSource,
    positionDimensions,
    positionConfidence,
    positionUnits,
    status: status as MeshNode['status'],
    battery: input.battery === undefined ? undefined : number(input.battery, `node ${id}.battery`),
    ip: typeof input.ip === 'string' ? input.ip : 'Unknown',
    hardware: typeof input.hardware === 'string' ? input.hardware : 'Unknown',
    neighborCount: input.neighborCount === undefined
      ? undefined
      : number(input.neighborCount, `node ${id}.neighborCount`),
    lastSeen: timestamp(input.lastSeen, `node ${id}.lastSeen`),
    sensors,
  };
}

function parseLink(value: unknown): MeshLink {
  const input = record(value, 'link');
  const id = string(input.id, 'link.id');
  if (typeof input.active !== 'boolean') throw new Error(`link ${id}.active must be a boolean`);
  const rssi = input.rssi === undefined ? undefined : number(input.rssi, `link ${id}.rssi`);
  return {
    id,
    source: string(input.source, `link ${id}.source`),
    target: string(input.target, `link ${id}.target`),
    ...(rssi === undefined ? {} : { rssi }),
    active: input.active,
  };
}

function parseAlert(value: unknown): MeshAlert {
  const input = record(value, 'alert');
  return {
    id: string(input.id, 'alert.id'),
    nodeId: string(input.nodeId, 'alert.nodeId'),
    kind: string(input.kind, 'alert.kind'),
    message: typeof input.message === 'string' ? input.message : '',
    occurredAt: timestamp(input.occurredAt, 'alert.occurredAt'),
    resolvedAt: input.resolvedAt === undefined
      ? undefined
      : timestamp(input.resolvedAt, 'alert.resolvedAt'),
  };
}

function parsePacket(value: unknown): HopPacket {
  const input = record(value, 'packet');
  const id = string(input.id ?? input.messageId, 'packet.id');
  const origin = string(input.origin, `packet ${id}.origin`);
  const target = string(input.target, `packet ${id}.target`);
  const status = input.status;
  if (!['queued', 'forwarding', 'awaiting_ack', 'failed'].includes(String(status))) {
    throw new Error(`packet ${id} has an unsupported status`);
  }
  let currentHop = target;
  let currentHopSource: string | undefined;
  let progress = 0;
  if (input.currentHop !== undefined) {
    const hop = record(input.currentHop, `packet ${id}.currentHop`);
    currentHop = string(hop.target, `packet ${id}.currentHop.target`);
    currentHopSource = typeof hop.source === 'string'
      ? string(hop.source, `packet ${id}.currentHop.source`)
      : undefined;
    progress = number(hop.progress, `packet ${id}.currentHop.progress`);
    if (progress < 0 || progress > 1) throw new Error(`packet ${id} progress must be between 0 and 1`);
  }
  const route = input.path === undefined
    ? currentHopSource ? [currentHopSource, currentHop] : [origin, currentHop]
    : array(input.path, `packet ${id}.path`).map((node, index) =>
        string(node, `packet ${id}.path[${index}]`),
      );
  const payloadInput = input.payload === undefined ? {} : record(input.payload, `packet ${id}.payload`);
  const payloadType = payloadInput.type;
  const supportedType = ['HEARTBEAT', 'SOS', 'DATA'].includes(String(payloadType))
    ? payloadType as HopPacket['payload']['type']
    : 'DATA';

  return {
    id,
    requestId: typeof input.requestId === 'string' ? input.requestId : undefined,
    origin,
    target,
    currentHop,
    currentHopSource,
    path: route,
    ttl: input.ttl === undefined ? 0 : number(input.ttl, `packet ${id}.ttl`),
    progress,
    status: status as NonNullable<HopPacket['status']>,
    failureReason: typeof input.reason === 'string' ? input.reason : undefined,
    payload: {
      type: supportedType,
      message: typeof payloadInput.message === 'string' ? payloadInput.message : '',
      timestamp: payloadInput.timestamp === undefined
        ? Date.now()
        : timestamp(payloadInput.timestamp, `packet ${id}.payload.timestamp`),
    },
  };
}

function parseNodes(value: unknown): MeshNode[] {
  return array(value, 'nodes').map(parseNode);
}

function parseLinks(value: unknown): MeshLink[] {
  return array(value, 'links').map(parseLink);
}

export function parseBridgeEvent(value: unknown): BridgeEvent {
  const input = record(value, 'bridge event');
  const type = string(input.type, 'bridge event.type');
  switch (type) {
    case 'STATE_SNAPSHOT':
      return {
        type,
        nodes: parseNodes(input.nodes),
        links: parseLinks(input.links),
        alerts: input.alerts === undefined ? [] : array(input.alerts, 'alerts').map(parseAlert),
        packets: input.packets === undefined ? [] : array(input.packets, 'packets').map(parsePacket),
      };
    case 'TOPOLOGY_UPDATE':
      return { type, nodes: parseNodes(input.nodes), links: parseLinks(input.links) };
    case 'PEER_UPDATE':
      return { type, node: parseNode(input.node) };
    case 'ALERT_RAISED':
      return { type, alert: parseAlert(input.alert) };
    case 'ALERT_CLEARED':
      return {
        type,
        alertId: string(input.alertId, 'alertId'),
        occurredAt: timestamp(input.occurredAt, 'occurredAt'),
      };
    case 'PACKET_STATE':
      return { type, packet: parsePacket(input.packet) };
    case 'SEND_ACCEPTED':
      return {
        type,
        requestId: string(input.requestId, 'requestId'),
        messageId: string(input.messageId, 'messageId'),
      };
    case 'SEND_REJECTED':
      return {
        type,
        requestId: string(input.requestId, 'requestId'),
        reason: string(input.reason, 'reason'),
      };
    case 'DELIVERY_ACK':
      return {
        type,
        messageId: string(input.messageId, 'messageId'),
        nodeId: string(input.nodeId, 'nodeId'),
        response: typeof input.response === 'string' ? input.response : undefined,
        receivedAt: timestamp(input.receivedAt, 'receivedAt'),
      };
    case 'MESSAGE_ECHO':
      return {
        type,
        messageId: string(input.messageId, 'messageId'),
        nodeId: string(input.nodeId, 'nodeId'),
        payload: typeof input.payload === 'string' ? input.payload : '',
        receivedAt: timestamp(input.receivedAt, 'receivedAt'),
      };
    default:
      throw new Error(`Unsupported bridge event type: ${type}`);
  }
}
