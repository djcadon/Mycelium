import { useState, useEffect, useRef } from 'react';
import { Canvas } from '@react-three/fiber';
import { MeshScene } from './components/MeshScene';
import { NetworkHUD } from './components/NetworkHUD';
import type { MeshNode, MeshLink, HopPacket } from './types/mesh';

export function App() {
  const [nodes, setNodes] = useState<MeshNode[]>([]);
  const [links, setLinks] = useState<MeshLink[]>([]);
  const [packets, setPackets] = useState<HopPacket[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [isLiveConnected, setIsLiveConnected] = useState<boolean>(false);

  const wsRef = useRef<WebSocket | null>(null);

  // Live Hardware WebSocket Bridge
  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimeout: ReturnType<typeof setTimeout>;

    const connectWebSocket = () => {
      try {
        ws = new WebSocket('ws://localhost:8765');
        wsRef.current = ws;

        ws.onopen = () => {
          console.log('[Mycelium] Connected to Live Hardware WebSocket Bridge (COM4)!');
          setIsLiveConnected(true);
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === 'TOPOLOGY_UPDATE') {
              if (data.nodes && Array.isArray(data.nodes)) {
                setNodes(data.nodes);
                if (data.nodes.length > 0) {
                  setSelectedNodeId((prev) => prev || data.nodes[0].id);
                }
              }
              if (data.links && Array.isArray(data.links)) {
                setLinks(data.links);
              }
            } else if (data.type === 'PACKET_DISPATCH') {
              if (data.packet) {
                setPackets((prev) => [...prev, data.packet]);
              }
            }
          } catch (err) {
            console.error('[WS Parse Error]', err);
          }
        };

        ws.onclose = () => {
          setIsLiveConnected(false);
          reconnectTimeout = setTimeout(connectWebSocket, 2000);
        };

        ws.onerror = () => {
          setIsLiveConnected(false);
        };
      } catch {
        setIsLiveConnected(false);
        reconnectTimeout = setTimeout(connectWebSocket, 2000);
      }
    };

    connectWebSocket();

    return () => {
      if (ws) ws.close();
      clearTimeout(reconnectTimeout);
    };
  }, []);

  // Animation engine for packet hops across 3D curves
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
              return null;
            }
            return { ...p, progress: nextProgress };
          })
          .filter(Boolean) as HopPacket[];
      });
    }, 30);

    return () => clearInterval(interval);
  }, []);

  const handleTriggerPacket = (originId: string, targetId: string, type: 'HEARTBEAT' | 'SOS' | 'DATA') => {
    const newPacket: HopPacket = {
      id: `pkt-${Date.now()}`,
      origin: originId,
      target: targetId,
      currentHop: targetId,
      path: [originId, targetId],
      ttl: 5,
      progress: 0,
      payload: {
        type,
        message: type === 'SOS' ? 'EMERGENCY BEACON' : 'PACKET HOP',
        timestamp: Date.now(),
      },
    };
    setPackets((prev) => [...prev, newPacket]);
  };

  const handleToggleNodeStatus = (nodeId: string) => {
    setNodes((prev) =>
      prev.map((n) => (n.id === nodeId ? { ...n, status: n.status === 'offline' ? 'online' : 'offline' } : n))
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
        isLiveConnected={isLiveConnected}
        onTriggerPacket={handleTriggerPacket}
        onToggleNodeStatus={handleToggleNodeStatus}
        selectedNode={selectedNode}
      />
    </div>
  );
}

export default App;
