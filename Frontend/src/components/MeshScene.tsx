import React, { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { OrbitControls, Html, Sphere, Ring } from '@react-three/drei';
import * as THREE from 'three';
import type { MeshNode, MeshLink, HopPacket } from '../types/mesh';

interface MeshSceneProps {
  nodes: MeshNode[];
  links: MeshLink[];
  packets: HopPacket[];
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

const getPeerColor = (status: MeshNode['status']) => {
  switch (status) {
    case 'offline': return '#64748b';       // Gray
    case 'alert': return '#ef4444';         // Red
    case 'transmitting': return '#f59e0b';  // Amber
    case 'online':
    default: return '#10b981';              // Emerald (Equal peer)
  }
};

const NodeMesh: React.FC<{
  node: MeshNode;
  isSelected: boolean;
  onSelect: () => void;
}> = ({ node, isSelected, onSelect }) => {
  const meshRef = useRef<THREE.Group>(null);
  const color = getPeerColor(node.status);

  useFrame((state) => {
    if (meshRef.current && node.status !== 'offline') {
      meshRef.current.rotation.y += 0.01;
      if (isSelected) {
        const s = 1 + Math.sin(state.clock.elapsedTime * 6) * 0.08;
        meshRef.current.scale.set(s, s, s);
      }
    }
  });

  return (
    <group position={node.position} ref={meshRef} onClick={(e) => { e.stopPropagation(); onSelect(); }}>
      {/* Peer Core Sphere */}
      <Sphere args={[0.55, 32, 32]}>
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={node.status === 'offline' ? 0.05 : isSelected ? 0.9 : 0.45}
          roughness={0.2}
          metalness={0.8}
        />
      </Sphere>

      {/* Peer Halo Ring */}
      {node.status !== 'offline' && (
        <Ring args={[0.75, 0.85, 32]} rotation={[Math.PI / 2, 0, 0]}>
          <meshBasicMaterial
            color={color}
            side={THREE.DoubleSide}
            transparent
            opacity={isSelected ? 0.8 : 0.35}
          />
        </Ring>
      )}

      {/* Peer Label Billboarding */}
      <Html position={[0, 0.95, 0]} center distanceFactor={15} style={{ pointerEvents: 'none' }}>
        <div className={`px-2.5 py-0.5 rounded text-xs font-mono font-bold whitespace-nowrap shadow-md backdrop-blur border ${
          isSelected 
            ? 'bg-amber-500/20 border-amber-400 text-amber-300' 
            : 'bg-slate-900/80 border-slate-700/80 text-slate-200'
        }`}>
          {node.name}
          {node.status === 'alert' && <span className="ml-1 text-red-400 animate-pulse">● SOS</span>}
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
    new THREE.Vector3(...sourceNode.position),
    new THREE.Vector3(...targetNode.position),
  ];
  const geometry = new THREE.BufferGeometry().setFromPoints(points);

  const getLinkColor = (rssi: number) => {
    if (rssi > -60) return '#10b981'; // strong (green)
    if (rssi > -75) return '#f59e0b'; // medium (amber)
    return '#ef4444';                 // weak (red)
  };

  return (
    <primitive object={new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({
        color: getLinkColor(link.rssi),
        transparent: true,
        opacity: link.active ? 0.65 : 0.1,
        linewidth: 2,
      })
    )} />
  );
};

const PacketParticle: React.FC<{
  packet: HopPacket;
  nodes: MeshNode[];
}> = ({ packet, nodes }) => {
  const meshRef = useRef<THREE.Mesh>(null);

  const sourceNode = nodes.find((n) => n.id === packet.origin);
  const targetNode = nodes.find((n) => n.id === packet.target);

  useFrame(() => {
    if (!meshRef.current || !sourceNode || !targetNode) return;
    const p1 = new THREE.Vector3(...sourceNode.position);
    const p2 = new THREE.Vector3(...targetNode.position);
    const currentPos = new THREE.Vector3().lerpVectors(p1, p2, packet.progress);
    currentPos.y += Math.sin(packet.progress * Math.PI) * 0.8;
    meshRef.current.position.copy(currentPos);
  });

  if (!sourceNode || !targetNode) return null;

  const isSos = packet.payload.type === 'SOS';

  return (
    <Sphere ref={meshRef} args={[0.22, 16, 16]}>
      <meshBasicMaterial
        color={isSos ? '#ef4444' : '#38bdf8'}
        transparent
        opacity={0.9}
      />
    </Sphere>
  );
};

export const MeshScene: React.FC<MeshSceneProps> = ({
  nodes,
  links,
  packets,
  selectedNodeId,
  onSelectNode,
}) => {
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  return (
    <>
      <ambientLight intensity={0.4} />
      <pointLight position={[10, 15, 10]} intensity={1.2} />
      <pointLight position={[-10, -10, -10]} intensity={0.5} color="#10b981" />
      <gridHelper args={[30, 30, '#1e293b', '#0f172a']} position={[0, -2, 0]} />

      <OrbitControls
        makeDefault
        minDistance={4}
        maxDistance={40}
        maxPolarAngle={Math.PI / 2 + 0.1}
      />

      {/* Mesh peer links */}
      {links.map((link) => (
        <LinkLine
          key={link.id}
          link={link}
          sourceNode={nodeMap.get(link.source)}
          targetNode={nodeMap.get(link.target)}
        />
      ))}

      {/* Equal peer nodes */}
      {nodes.map((node) => (
        <NodeMesh
          key={node.id}
          node={node}
          isSelected={node.id === selectedNodeId}
          onSelect={() => onSelectNode(node.id)}
        />
      ))}

      {/* In-flight peer packets */}
      {packets.map((packet) => (
        <PacketParticle key={packet.id} packet={packet} nodes={nodes} />
      ))}
    </>
  );
};
