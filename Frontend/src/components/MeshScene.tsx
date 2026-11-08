import React, { useEffect, useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Bounds, Html, OrbitControls, Ring, Sphere, useBounds } from '@react-three/drei';
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

const BoundsController: React.FC<{ nodeCount: number }> = ({ nodeCount }) => {
  const bounds = useBounds();
  const prevCount = useRef(nodeCount);

  useEffect(() => {
    if (prevCount.current !== nodeCount) {
      prevCount.current = nodeCount;
      bounds.refresh().fit();
    }
  }, [nodeCount, bounds]);

  return null;
};

const NodeMesh: React.FC<{
  node: MeshNode;
  isSelected: boolean;
  hasAlert: boolean;
  onSelect: () => void;
  nodePositionsRef: React.MutableRefObject<Map<string, THREE.Vector3>>;
}> = ({ node, isSelected, hasAlert, onSelect, nodePositionsRef }) => {
  const meshRef = useRef<THREE.Group>(null);
  const color = hasAlert ? '#a9563e' : getPeerColor(node.status);
  const isGateway = node.id === 'Node-0000' ||
    node.id === 'Laptop-Base' ||
    node.hardware.toLowerCase().includes('laptop') ||
    node.hardware.toLowerCase().includes('bridge') ||
    node.hardware.toLowerCase().includes('host');

  const initialPosition = useRef<[number, number, number] | null>(null);
  if (!initialPosition.current) {
    const existing = nodePositionsRef.current.get(node.id);
    initialPosition.current = existing
      ? [existing.x, existing.y, existing.z]
      : [node.position[0], node.position[1], node.position[2]];
  }

  useFrame((state) => {
    if (!meshRef.current) return;
    const pos = nodePositionsRef.current.get(node.id);
    if (pos) {
      meshRef.current.position.copy(pos);
    }
    const scale = isSelected ? 1 + Math.sin(state.clock.elapsedTime * 3) * 0.025 : 1;
    meshRef.current.scale.setScalar(scale);
  });

  return (
    <group position={initialPosition.current} ref={meshRef}>
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

      <Html position={[0, 0.12, -0.46]} center zIndexRange={[10, 0]}>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onSelect();
          }}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded border text-sm font-semibold whitespace-nowrap shadow-sm cursor-pointer transition-transform hover:scale-105 ${
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
  isHighlighted?: boolean;
  isDimmed?: boolean;
  nodePositionsRef: React.MutableRefObject<Map<string, THREE.Vector3>>;
}> = ({ link, isHighlighted, isDimmed, nodePositionsRef }) => {
  const lineObject = useMemo(() => {
    const geom = new THREE.BufferGeometry();
    const positions = new Float32Array(6);
    const p1 = nodePositionsRef.current.get(link.source);
    const p2 = nodePositionsRef.current.get(link.target);
    if (p1 && p2) {
      positions[0] = p1.x;
      positions[1] = p1.y + 0.035;
      positions[2] = p1.z;
      positions[3] = p2.x;
      positions[4] = p2.y + 0.035;
      positions[5] = p2.z;
    }
    geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.LineBasicMaterial({
      color: '#999487',
      transparent: true,
      opacity: 0,
    });
    return new THREE.Line(geom, mat);
  }, [link.source, link.target, nodePositionsRef]);

  useEffect(() => {
    return () => {
      lineObject.geometry.dispose();
      (lineObject.material as THREE.Material).dispose();
    };
  }, [lineObject]);

  const color = isHighlighted
    ? '#775a3e'
    : link.rssi === undefined
      ? '#999487'
      : link.rssi > -60
        ? '#536b43'
        : link.rssi > -75
          ? '#b28a49'
          : '#a9563e';

  useEffect(() => {
    (lineObject.material as THREE.LineBasicMaterial).color.set(color);
  }, [lineObject, color]);

  const targetOpacity = isHighlighted ? 0.95 : isDimmed ? 0.25 : 0.76;

  useFrame((_, delta) => {
    const p1 = nodePositionsRef.current.get(link.source);
    const p2 = nodePositionsRef.current.get(link.target);
    if (p1 && p2) {
      const posAttr = lineObject.geometry.attributes.position as THREE.BufferAttribute;
      const array = posAttr.array as Float32Array;
      array[0] = p1.x;
      array[1] = p1.y + 0.035;
      array[2] = p1.z;
      array[3] = p2.x;
      array[4] = p2.y + 0.035;
      array[5] = p2.z;
      posAttr.needsUpdate = true;
    }

    const mat = lineObject.material as THREE.LineBasicMaterial;
    if (mat) {
      const safeDelta = Math.min(delta, 0.1);
      mat.opacity = THREE.MathUtils.lerp(
        mat.opacity,
        targetOpacity,
        1 - Math.exp(-12 * safeDelta),
      );
    }
  });

  return <primitive object={lineObject} />;
};

export const MeshScene: React.FC<MeshSceneProps> = ({
  nodes,
  links,
  packets,
  alerts,
  selectedNodeId,
  onSelectNode,
}) => {
  const alertNodeIds = useMemo(
    () => new Set(alerts.filter((alert) => alert.resolvedAt === undefined).map((alert) => alert.nodeId)),
    [alerts],
  );

  const nodePositionsRef = useRef<Map<string, THREE.Vector3>>(new Map());
  const tempVec = useMemo(() => new THREE.Vector3(), []);

  // Smooth position interpolation loop
  useFrame((_, delta) => {
    const safeDelta = Math.min(delta, 0.1);
    // Smooth glide factor: reaching destination in ~0.45s with natural deceleration
    const lerpFactor = 1 - Math.exp(-6.5 * safeDelta);

    for (const node of nodes) {
      let current = nodePositionsRef.current.get(node.id);
      if (!current) {
        current = new THREE.Vector3(node.position[0], node.position[1], node.position[2]);
        nodePositionsRef.current.set(node.id, current);
      } else {
        tempVec.set(node.position[0], node.position[1], node.position[2]);
        if (current.distanceToSquared(tempVec) < 0.00001) {
          current.copy(tempVec);
        } else {
          current.lerp(tempVec, lerpFactor);
        }
      }
    }

    // Clean up nodes no longer in graph
    if (nodePositionsRef.current.size > nodes.length) {
      const activeIds = new Set(nodes.map((n) => n.id));
      for (const id of Array.from(nodePositionsRef.current.keys())) {
        if (!activeIds.has(id)) {
          nodePositionsRef.current.delete(id);
        }
      }
    }
  });

  const uniqueLinks = useMemo(() => {
    const linkMap = new Map<string, { link: MeshLink; pairKey: string }>();
    for (const link of links) {
      if (!link.active || link.source === link.target) continue;
      const pairKey = [link.source, link.target].sort().join('--');
      const existing = linkMap.get(pairKey);
      if (!existing) {
        linkMap.set(pairKey, { link, pairKey });
      } else {
        const existingRssi = existing.link.rssiMedian ?? existing.link.rssi;
        const currentRssi = link.rssiMedian ?? link.rssi;
        if (currentRssi !== undefined && (existingRssi === undefined || currentRssi > existingRssi)) {
          linkMap.set(pairKey, { link, pairKey });
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
        <Bounds fit observe margin={1.12}>
          <BoundsController nodeCount={nodes.length} />
          <group>
            {uniqueLinks.map(({ link, pairKey }) => {
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
                  key={pairKey}
                  link={link}
                  isHighlighted={isHighlighted}
                  isDimmed={isDimmed}
                  nodePositionsRef={nodePositionsRef}
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
                nodePositionsRef={nodePositionsRef}
              />
            ))}
          </group>
        </Bounds>
      )}
    </>
  );
};
