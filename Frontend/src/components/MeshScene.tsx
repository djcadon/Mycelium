import React, { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Bounds, Html, OrbitControls, Ring, Sphere } from '@react-three/drei';
import * as THREE from 'three';
import type { MeshAlert, MeshNode, MeshLink, HopPacket } from '../types/mesh';

interface MeshSceneProps {
  nodes: MeshNode[];
  links: MeshLink[];
  packets: HopPacket[];
  alerts: MeshAlert[];
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

const getPeerColor = (status: MeshNode['status']) => {
  switch (status) {
    case 'offline': return '#999487';
    case 'alert': return '#a9563e';
    case 'transmitting': return '#b28a49';
    case 'online':
    default: return '#536b43';
  }
};

const NodeMesh: React.FC<{
  node: MeshNode;
  isSelected: boolean;
  hasAlert: boolean;
  onSelect: () => void;
}> = ({ node, isSelected, hasAlert, onSelect }) => {
  const meshRef = useRef<THREE.Group>(null);
  const color = hasAlert ? '#a9563e' : getPeerColor(node.status);
  const isGateway = node.id === 'Node-0000' ||
    node.id === 'Laptop-Base' ||
    node.hardware.toLowerCase().includes('laptop') ||
    node.hardware.toLowerCase().includes('bridge') ||
    node.hardware.toLowerCase().includes('host');

  useFrame((state) => {
    if (!meshRef.current) return;
    const scale = isSelected ? 1 + Math.sin(state.clock.elapsedTime * 3) * 0.025 : 1;
    meshRef.current.scale.setScalar(scale);
  });

  return (
    <group position={node.position} ref={meshRef}>
      <Sphere
        position={[0, 0.12, 0]}
        args={[0.52, 16, 16]}
        onClick={(event) => {
          event.stopPropagation();
          onSelect();
        }}
      >
        <meshBasicMaterial transparent opacity={0} depthWrite={false} colorWrite={false} />
      </Sphere>

      <Sphere position={[0, 0.12, 0]} args={[isGateway ? 0.28 : 0.24, 24, 24]}>
        <meshStandardMaterial color={color} roughness={0.88} metalness={0} />
      </Sphere>

      <Ring
        args={[0.36, 0.39, 32]}
        position={[0, 0.025, 0]}
        rotation={[Math.PI / 2, 0, 0]}
      >
        <meshBasicMaterial
          color={isSelected ? '#775a3e' : color}
          side={THREE.DoubleSide}
          transparent
          opacity={isSelected ? 0.85 : 0.48}
        />
      </Ring>

      {isGateway && (
        <Ring
          args={[0.45, 0.48, 32]}
          position={[0, 0.02, 0]}
          rotation={[Math.PI / 2, 0, 0]}
        >
          <meshBasicMaterial
            color="#536b43"
            side={THREE.DoubleSide}
            transparent
            opacity={0.35}
          />
        </Ring>
      )}

      <Html position={[0, 0.12, -0.46]} center>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onSelect();
          }}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded border text-sm font-semibold whitespace-nowrap shadow-sm cursor-pointer transition-all hover:scale-105 ${
            isSelected
              ? 'border-[#8c744c] text-forest bg-paper ring-1 ring-[#8c744c]'
              : 'border-[#d5d0c0] text-[#394438] bg-paper/95 hover:border-moss hover:bg-paper'
          }`}
          title={`Select ${node.name}`}
        >
          <span>{node.name}</span>
          {isGateway && (
            <span className="text-xs font-mono uppercase px-1.5 py-0.5 rounded bg-moss/20 text-moss font-bold ml-1">Gateway</span>
          )}
          {(hasAlert || node.status === 'alert') && (
            <span className="text-clay text-xs font-bold tracking-wider ml-1">SOS</span>
          )}
        </button>
      </Html>
    </group>
  );
};

const LinkLine: React.FC<{
  link: MeshLink;
  sourceNode?: MeshNode;
  targetNode?: MeshNode;
  isHighlighted?: boolean;
  isDimmed?: boolean;
}> = ({ link, sourceNode, targetNode, isHighlighted, isDimmed }) => {
  if (!sourceNode || !targetNode || !link.active) return null;

  const points = [
    new THREE.Vector3(sourceNode.position[0], sourceNode.position[1] + 0.035, sourceNode.position[2]),
    new THREE.Vector3(targetNode.position[0], targetNode.position[1] + 0.035, targetNode.position[2]),
  ];
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const color = isHighlighted
    ? '#775a3e'
    : link.rssi === undefined
      ? '#999487'
      : link.rssi > -60
        ? '#536b43'
        : link.rssi > -75
          ? '#b28a49'
          : '#a9563e';

  const opacity = isHighlighted ? 0.95 : isDimmed ? 0.25 : 0.76;

  return (
    <primitive
      object={new THREE.Line(
        geometry,
        new THREE.LineBasicMaterial({
          color,
          transparent: true,
          opacity,
        }),
      )}
    />
  );
};

const PacketParticle: React.FC<{
  packet: HopPacket;
  nodes: MeshNode[];
}> = ({ packet, nodes }) => {
  const meshRef = useRef<THREE.Mesh>(null);
  const targetIndex = packet.path.indexOf(packet.currentHop);
  const sourceId = packet.currentHopSource ??
    (targetIndex > 0 ? packet.path[targetIndex - 1] : packet.origin);
  const sourceNode = nodes.find((node) => node.id === sourceId);
  const targetNode = nodes.find((node) => node.id === packet.currentHop);

  useFrame(() => {
    if (!meshRef.current || !sourceNode || !targetNode || packet.status !== 'forwarding') return;
    const start = new THREE.Vector3(...sourceNode.position);
    const end = new THREE.Vector3(...targetNode.position);
    const position = new THREE.Vector3().lerpVectors(start, end, packet.progress);
    position.y += 0.3;
    meshRef.current.position.copy(position);
  });

  if (!sourceNode || !targetNode) return null;

  return (
    <Sphere ref={meshRef} args={[0.075, 12, 12]}>
      <meshBasicMaterial color={packet.payload.type === 'SOS' ? '#a9563e' : '#b28a49'} />
    </Sphere>
  );
};

export const MeshScene: React.FC<MeshSceneProps> = ({
  nodes,
  links,
  packets,
  alerts,
  selectedNodeId,
  onSelectNode,
}) => {
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const alertNodeIds = new Set(alerts.filter((alert) => alert.resolvedAt === undefined).map((alert) => alert.nodeId));
  const layoutKey = nodes
    .map((node) => `${node.id}:${node.position.join(',')}`)
    .join('|');

  const uniqueLinks = useMemo(() => {
    const linkMap = new Map<string, MeshLink>();
    for (const link of links) {
      if (!link.active || link.source === link.target) continue;
      const pairKey = [link.source, link.target].sort().join('--');
      const existing = linkMap.get(pairKey);
      if (!existing) {
        linkMap.set(pairKey, link);
      } else {
        const existingRssi = existing.rssiMedian ?? existing.rssi;
        const currentRssi = link.rssiMedian ?? link.rssi;
        if (currentRssi !== undefined && (existingRssi === undefined || currentRssi > existingRssi)) {
          linkMap.set(pairKey, link);
        }
      }
    }
    return Array.from(linkMap.values());
  }, [links]);

  return (
    <>
      <ambientLight intensity={1.6} />
      <directionalLight position={[3, 8, 4]} intensity={1.1} color="#fff9e9" />

      <OrbitControls
        makeDefault
        enableRotate={false}
        minZoom={0.1}
        maxZoom={100}
        mouseButtons={{
          LEFT: THREE.MOUSE.PAN,
          MIDDLE: THREE.MOUSE.DOLLY,
          RIGHT: THREE.MOUSE.PAN,
        }}
        enableDamping
        dampingFactor={0.08}
      />

      {nodes.length > 0 && (
        <Bounds key={layoutKey} fit observe margin={1.12}>
          <group>
            {uniqueLinks.map((link) => {
              const isHighlighted = Boolean(
                selectedNodeId &&
                (link.source === selectedNodeId || link.target === selectedNodeId),
              );
              const isDimmed = Boolean(
                selectedNodeId &&
                link.source !== selectedNodeId &&
                link.target !== selectedNodeId,
              );
              return (
                <LinkLine
                  key={link.id}
                  link={link}
                  sourceNode={nodeMap.get(link.source)}
                  targetNode={nodeMap.get(link.target)}
                  isHighlighted={isHighlighted}
                  isDimmed={isDimmed}
                />
              );
            })}

            {nodes.map((node) => (
              <NodeMesh
                key={node.id}
                node={node}
                isSelected={node.id === selectedNodeId}
                hasAlert={alertNodeIds.has(node.id)}
                onSelect={() => onSelectNode(node.id)}
              />
            ))}
          </group>
        </Bounds>
      )}

      {packets.filter((packet) => packet.status === 'forwarding' && packet.progress >= 0).map((packet) => (
        <PacketParticle key={packet.id} packet={packet} nodes={nodes} />
      ))}
    </>
  );
};
