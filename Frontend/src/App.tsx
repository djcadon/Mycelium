import { useState, useEffect } from 'react';
import { Canvas } from '@react-three/fiber';
import { MeshScene } from './components/MeshScene';
import { NetworkHUD } from './components/NetworkHUD';
import type { MeshNode, MeshLink, HopPacket } from './types/mesh';

const INITIAL_NODES: MeshNode[] = [
  {
    id: 'peer-1',
    name: 'Peer-01 (Uno Q)',
    position: [-4.5, 0, 0],
    status: 'online',
    battery: 100,
    ip: '192.168.99.1',
    hardware: 'Uno Q',
    sensors: { temperature: 23, sosPressed: false },
    neighborCount: 2,
    lastSeen: Date.now(),
  },
  {
    id: 'peer-2',
    name: 'Peer-02 (Uno Q)',
    position: [0, 2.2, 2],
    status: 'online',
    battery: 92,
    ip: '192.168.99.2',
    hardware: 'Uno Q',
    sensors: { temperature: 25, sosPressed: false },
    neighborCount: 3,
    lastSeen: Date.now(),
  },
  {
    id: 'peer-3',
    name: 'Peer-03 (Uno Q)',
    position: [0, -2.2, -2],
    status: 'online',
    battery: 88,
    ip: '192.168.99.3',
    hardware: 'Uno Q',
    sensors: { temperature: 24, sosPressed: false },
    neighborCount: 3,
    lastSeen: Date.now(),
  },
  {
    id: 'peer-4',
    name: 'Peer-04 (Uno Q)',
    position: [4.5, 0, 0],
    status: 'online',
    battery: 79,
    ip: '192.168.99.4',
    hardware: 'Uno Q',
    sensors: { temperature: 27, sosPressed: false },
    neighborCount: 2,
    lastSeen: Date.now(),
  },
];

const INITIAL_LINKS: MeshLink[] = [
  { id: 'l1', source: 'peer-1', target: 'peer-2', rssi: -52, active: true },
  { id: 'l2', source: 'peer-1', target: 'peer-3', rssi: -65, active: true },
  { id: 'l3', source: 'peer-2', target: 'peer-4', rssi: -58, active: true },
  { id: 'l4', source: 'peer-3', target: 'peer-4', rssi: -71, active: true },
  { id: 'l5', source: 'peer-2', target: 'peer-3', rssi: -60, active: true },
];

export function App() {
  const [nodes, setNodes] = useState<MeshNode[]>(INITIAL_NODES);
  const [links, setLinks] = useState<MeshLink[]>(INITIAL_LINKS);
  const [packets, setPackets] = useState<HopPacket[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>('peer-1');

  // Animation ticker for packet hops
  useEffect(() => {
    const interval = setInterval(() => {
      setPackets((prevPackets) => {
        if (prevPackets.length === 0) return prevPackets;

        return prevPackets
          .map((p) => {
            const nextProgress = p.progress + 0.04;
            if (nextProgress >= 1.0) {
              const currentIndex = p.path.indexOf(p.target);
              if (currentIndex !== -1 && currentIndex < p.path.length - 1) {
                // Flash the intermediate peer
                const intermediatePeer = p.path[currentIndex];
                setNodes((allNodes) =>
                  allNodes.map((n) =>
                    n.id === intermediatePeer ? { ...n, status: 'transmitting' } : n
                  )
                );
                setTimeout(() => {
                  setNodes((allNodes) =>
                    allNodes.map((n) =>
                      n.id === intermediatePeer && n.status === 'transmitting'
                        ? { ...n, status: 'online' }
                        : n
                    )
                  );
                }, 400);

                return {
                  ...p,
                  origin: p.path[currentIndex],
                  target: p.path[currentIndex + 1],
                  currentHop: p.path[currentIndex + 1],
                  progress: 0.0,
                };
              }
              return null; // Delivered to destination peer
            }
            return { ...p, progress: nextProgress };
          })
          .filter(Boolean) as HopPacket[];
      });
    }, 30);

    return () => clearInterval(interval);
  }, []);

  // Compute active route dynamically (self-healing demo)
  const computeRoute = (start: string, end: string): string[] => {
    const isPeer2Online = nodes.find((n) => n.id === 'peer-2')?.status !== 'offline';
    const isPeer3Online = nodes.find((n) => n.id === 'peer-3')?.status !== 'offline';

    if (start === 'peer-1' && end === 'peer-4') {
      if (isPeer2Online) return ['peer-1', 'peer-2', 'peer-4'];
      if (isPeer3Online) return ['peer-1', 'peer-3', 'peer-4'];
    }
    if (start === 'peer-4' && end === 'peer-1') {
      if (isPeer3Online) return ['peer-4', 'peer-3', 'peer-1'];
      if (isPeer2Online) return ['peer-4', 'peer-2', 'peer-1'];
    }
    return [start, end];
  };

  const handleTriggerPacket = (originId: string, targetId: string, type: 'HEARTBEAT' | 'SOS' | 'DATA') => {
    const path = computeRoute(originId, targetId);
    if (path.length < 2) return;

    const newPacket: HopPacket = {
      id: `pkt-${Date.now()}`,
      origin: path[0],
      target: path[1],
      currentHop: path[1],
      path,
      ttl: 5,
      progress: 0,
      payload: {
        type,
        message: type === 'SOS' ? 'PEER SOS EMERGENCY' : 'PEER DATA RELAY',
        timestamp: Date.now(),
      },
    };

    setPackets((prev) => [...prev, newPacket]);

    if (type === 'SOS') {
      setNodes((prev) =>
        prev.map((n) => (n.id === originId ? { ...n, status: 'alert' } : n))
      );
    }
  };

  const handleToggleNodeStatus = (nodeId: string) => {
    setNodes((prev) =>
      prev.map((n) => {
        if (n.id === nodeId) {
          const nextStatus = n.status === 'offline' ? 'online' : 'offline';
          return { ...n, status: nextStatus };
        }
        return n;
      })
    );

    setLinks((prev) =>
      prev.map((l) => {
        if (l.source === nodeId || l.target === nodeId) {
          return { ...l, active: !l.active };
        }
        return l;
      })
    );
  };

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) || null;

  return (
    <div className="relative w-screen h-screen bg-[#060913] select-none">
      <Canvas
        camera={{ position: [0, 6, 14], fov: 45 }}
        style={{ width: '100%', height: '100%' }}
      >
        <MeshScene
          nodes={nodes}
          links={links}
          packets={packets}
          selectedNodeId={selectedNodeId}
          onSelectNode={(id) => setSelectedNodeId(id)}
        />
      </Canvas>

      <NetworkHUD
        nodes={nodes}
        links={links}
        packets={packets}
        onTriggerPacket={handleTriggerPacket}
        onToggleNodeStatus={handleToggleNodeStatus}
        selectedNode={selectedNode}
      />
    </div>
  );
}

export default App;
