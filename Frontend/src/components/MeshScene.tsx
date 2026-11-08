import React, { useRef } from 'react';
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

      <Sphere position={[0, 0.12, 0]} args={[0.24, 24, 24]}>
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
          opacity={isSelected ? 0.8 : 0.48}
        />
      </Ring>

      <Html position={[0, 0.12, -0.46]} center style={{ pointerEvents: 'none' }}>
        <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded border text-xs font-semibold whitespace-nowrap shadow-sm select-none ${
          isSelected
            ? 'border-[#8c744c] text-forest bg-paper/95 ring-1 ring-[#8c744c]'
            : 'border-[#d5d0c0] text-[#394438] bg-paper/90'
        }`}>
          <span>{node.name}</span>
          {(hasAlert || node.status === 'alert') && (
            <span className="text-clay text-[9px] font-bold tracking-wider ml-0.5">SOS</span>
          )}
        </div>
      </Html>
    </group>
  );
};

const LinkLine: React.FC<{
  link: MeshLink;
  sourceNode?: MeshNode;
  targetNode?: MeshNode;
}> = ({ link, sourceNode, targetNode }) => {
  if (!sourceNode || !targetNode || !link.active) return null;

  const points = [
    new THREE.Vector3(sourceNode.position[0], sourceNode.position[1] + 0.035, sourceNode.position[2]),
    new THREE.Vector3(targetNode.position[0], targetNode.position[1] + 0.035, targetNode.position[2]),
  ];
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const color = link.rssi === undefined
    ? '#999487'
    : link.rssi > -60
      ? '#536b43'
      : link.rssi > -75
        ? '#b28a49'
        : '#a9563e';

  return (
    <primitive
      object={new THREE.Line(
        geometry,
        new THREE.LineBasicMaterial({
          color,
          transparent: true,
          opacity: 0.76,
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
            {links.map((link) => (
              <LinkLine
                key={link.id}
                link={link}
                sourceNode={nodeMap.get(link.source)}
                targetNode={nodeMap.get(link.target)}
              />
            ))}

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
