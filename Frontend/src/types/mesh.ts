export type NodeStatus = 'online' | 'offline' | 'transmitting' | 'alert';

export interface MeshNode {
  id: string;
  name: string;
  position: [number, number, number];
  status: NodeStatus;
  battery: number; // percentage
  ip: string;
  hardware: 'Uno Q' | 'Virtual Node';
  sensors?: {
    temperature?: number;
    sosPressed?: boolean;
    vibration?: number;
  };
  // Decentralized peer metrics
  neighborCount: number;
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
  origin: string;
  target: string;
  currentHop: string;
  path: string[];
  ttl: number;
  progress: number; // 0.0 to 1.0 along current link
  payload: {
    type: 'HEARTBEAT' | 'SOS' | 'DATA';
    message: string;
    timestamp: number;
  };
}
