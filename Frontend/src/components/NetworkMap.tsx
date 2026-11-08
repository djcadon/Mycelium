import { useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import type { MeshAlert, MeshLink, MeshNode, HopPacket } from '../types/mesh';
import { MeshScene } from './MeshScene';

interface NetworkMapProps {
  nodes: MeshNode[];
  links: MeshLink[];
  packets: HopPacket[];
  alerts: MeshAlert[];
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

interface Point {
  x: number;
  y: number;
}

interface SpatialPoint {
  x: number;
  y: number;
  z: number;
}

function getTopologyLayout(nodes: MeshNode[], links: MeshLink[]): Map<string, Point> {
  const points = new Map<string, Point>();
  if (nodes.length === 0) return points;

  const root = nodes.find((node) => node.id === 'Laptop-Base') ?? nodes[0];
  const neighbors = new Map(nodes.map((node) => [node.id, new Set<string>()]));

  for (const link of links) {
    if (!link.active) continue;
    neighbors.get(link.source)?.add(link.target);
    neighbors.get(link.target)?.add(link.source);
  }

  const depth = new Map<string, number>([[root.id, 0]]);
  const queue = [root.id];

  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const nodeId = queue[cursor];
    const nextDepth = (depth.get(nodeId) ?? 0) + 1;
    for (const neighborId of neighbors.get(nodeId) ?? []) {
      if (depth.has(neighborId)) continue;
      depth.set(neighborId, nextDepth);
      queue.push(neighborId);
    }
  }

  for (const node of nodes) {
    if (!depth.has(node.id)) depth.set(node.id, 1);
  }

  const layers = new Map<number, MeshNode[]>();
  for (const node of nodes) {
    const layer = depth.get(node.id) ?? 1;
    const group = layers.get(layer) ?? [];
    group.push(node);
    layers.set(layer, group);
  }

  points.set(root.id, { x: 0, y: 0 });
  for (const [layer, group] of layers) {
    if (layer === 0) continue;
    const ordered = [...group].sort((a, b) => a.id.localeCompare(b.id));
    const radiusX = Math.min(190 + layer * 72, 420);
    const radiusY = Math.min(115 + layer * 48, 260);

    ordered.forEach((node, index) => {
      const angle = -Math.PI / 2 + (index / ordered.length) * Math.PI * 2;
      points.set(node.id, {
        x: Math.cos(angle) * radiusX,
        y: Math.sin(angle) * radiusY,
      });
    });
  }

  return points;
}

function getDisplayLayout(nodes: MeshNode[], links: MeshLink[]): Map<string, SpatialPoint> {
  const topologyPoints = getTopologyLayout(nodes, links);
  const rawPoints = new Map<string, SpatialPoint>();
  const hasCompleteCoordinates = nodes.length > 0 && nodes.every(
    (node) => node.positionSource === 'measured' || node.positionSource === 'estimated',
  );
  const hasComplete3DCoordinates = hasCompleteCoordinates && nodes.every(
    (node) => node.positionDimensions === 3,
  );

  for (const node of nodes) {
    if (hasComplete3DCoordinates) {
      rawPoints.set(node.id, {
        x: node.position[0],
        y: node.position[1],
        z: node.position[2],
      });
    } else if (hasCompleteCoordinates) {
      rawPoints.set(node.id, {
        x: node.position[0],
        y: 0,
        z: node.position[2],
      });
    } else {
      const point = topologyPoints.get(node.id) ?? { x: 0, y: 0 };
      rawPoints.set(node.id, { x: point.x, y: 0, z: point.y });
    }
  }

  if (rawPoints.size === 0) return rawPoints;
  const xs = [...rawPoints.values()].map((point) => point.x);
  const ys = [...rawPoints.values()].map((point) => point.y);
  const zs = [...rawPoints.values()].map((point) => point.z);
  const centerX = (Math.min(...xs) + Math.max(...xs)) / 2;
  const centerY = (Math.min(...ys) + Math.max(...ys)) / 2;
  const centerZ = (Math.min(...zs) + Math.max(...zs)) / 2;
  const span = Math.max(
    Math.max(...xs) - Math.min(...xs),
    Math.max(...ys) - Math.min(...ys),
    Math.max(...zs) - Math.min(...zs),
    1,
  );
  const scale = 16 / span;

  return new Map([...rawPoints.entries()].map(([id, point]) => [
    id,
    {
      x: (point.x - centerX) * scale,
      y: (point.y - centerY) * scale,
      z: (point.z - centerZ) * scale,
    },
  ]));
}

export function NetworkMap({
  nodes,
  links,
  packets,
  alerts,
  selectedNodeId,
  onSelectNode,
}: NetworkMapProps) {
  const points = useMemo(() => getDisplayLayout(nodes, links), [nodes, links]);
  const mapNodes = useMemo(
    () =>
      nodes.map((node) => {
        const point = points.get(node.id);
        return {
          ...node,
          position: point
            ? [point.x, point.y, point.z] as [number, number, number]
            : [0, 0, 0] as [number, number, number],
        };
      }),
    [nodes, points],
  );

  return (
    <div className="map-stage" role="group" aria-label="Top-down positional mesh map">
      <Canvas
        className="network-map"
        orthographic
        camera={{
          position: [0, 20, 0],
          up: [0, 0, -1],
        }}
        gl={{ alpha: true, antialias: true }}
        dpr={[1, 1.5]}
      >
        <MeshScene
          nodes={mapNodes}
          links={links}
          packets={packets}
          alerts={alerts}
          selectedNodeId={selectedNodeId}
          onSelectNode={onSelectNode}
        />
      </Canvas>

      {nodes.length === 0 && (
        <div className="map-empty">
          <span className="empty-mark" aria-hidden="true">✳</span>
          <p>Listening for the first peer</p>
          <span>Nodes will appear here as they are discovered.</span>
        </div>
      )}
    </div>
  );
}
