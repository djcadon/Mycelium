export type NodeStatus = 'online' | 'offline' | 'transmitting' | 'alert';

export interface MeshNode {
  id: string;
  name: string;
  position: [number, number, number];
  positionSource?: 'measured' | 'estimated' | 'diagrammatic';
  positionDimensions?: 2 | 3;
  positionConfidence?: number;
  positionUnits?: string;
  status: NodeStatus;
  battery?: number; // percentage
  ip: string;
  hardware: string;
  sensors?: {
    temperature?: number;
    sosPressed?: boolean;
    vibration?: number;
  };
  // Decentralized peer metrics
  neighborCount?: number;
  lastSeen: number;
}

export interface MeshLink {
  id: string;
  source: string;
  target: string;
  rssi: number; // dBm (e.g. -45 to -90)
  active: boolean;
}

export interface HopPacket {
  id: string;
  requestId?: string;
  origin: string;
  target: string;
  currentHop: string;
  currentHopSource?: string;
  path: string[];
  ttl: number;
  progress: number; // 0.0 to 1.0 along current link
  status?: 'queued' | 'forwarding' | 'awaiting_ack' | 'failed';
  failureReason?: string;
  payload: {
    type: 'HEARTBEAT' | 'SOS' | 'DATA';
    message: string;
    timestamp: number;
  };
}

export interface MeshAlert {
  id: string;
  nodeId: string;
  kind: string;
  message: string;
  occurredAt: number;
  resolvedAt?: number;
}

export type OutboundMessageStatus =
  | 'sending'
  | 'queued'
  | 'forwarding'
  | 'awaiting_ack'
  | 'unconfirmed'
  | 'delivered'
  | 'echo_received'
  | 'failed';

export interface OutboundMessage {
  requestId: string;
  messageId?: string;
  targetId: string;
  payload: string;
  status: OutboundMessageStatus;
  createdAt: number;
  response?: string;
  error?: string;
}
