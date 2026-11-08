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

  // 1. Identify Root (Gateway node)
  const root = nodes.find((node) =>
    node.id === 'Node-0000' ||
    node.id === 'Laptop-Base' ||
    node.hardware.toLowerCase().includes('laptop') ||
    node.hardware.toLowerCase().includes('bridge') ||
    node.hardware.toLowerCase().includes('host'),
  ) ?? nodes[0];

  // 2. Build Undirected Adjacency Graph from Active Links
  const adjacency = new Map<string, Set<string>>();
  for (const node of nodes) {
    adjacency.set(node.id, new Set<string>());
  }
  for (const link of links) {
    if (!link.active || link.source === link.target) continue;
    adjacency.get(link.source)?.add(link.target);
    adjacency.get(link.target)?.add(link.source);
  }

  // 3. BFS Tree to compute depths, parent pointers, and child hierarchy
  const depth = new Map<string, number>([[root.id, 0]]);
  const children = new Map<string, string[]>();
  for (const node of nodes) {
    children.set(node.id, []);
  }

  const queue = [root.id];
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const u = queue[cursor];
    const uDepth = depth.get(u) ?? 0;
    const neighbors = Array.from(adjacency.get(u) ?? []).sort((a, b) => a.localeCompare(b));
    for (const v of neighbors) {
      if (!depth.has(v)) {
        depth.set(v, uDepth + 1);
        children.get(u)?.push(v);
        queue.push(v);
      }
    }
  }

  // Any disconnected nodes get attached as layer 1 to root
  for (const node of nodes) {
    if (!depth.has(node.id)) {
      depth.set(node.id, 1);
      children.get(root.id)?.push(node.id);
    }
  }

  // 4. Initial Position Seeding: Angular Sector Distribution
  // Calculate subtree sizes for angular allocation around root
  const subtreeSize = new Map<string, number>();
  function calcSubtree(nodeId: string): number {
    let count = 1;
    for (const childId of children.get(nodeId) ?? []) {
      count += calcSubtree(childId);
    }
    subtreeSize.set(nodeId, count);
    return count;
  }
  calcSubtree(root.id);

  const nodePositions = new Map<string, { x: number; y: number }>();
  nodePositions.set(root.id, { x: 0, y: 0 });

  function assignPositions(
    nodeId: string,
    startAngle: number,
    endAngle: number,
    currentRadius: number,
  ) {
    const childList = children.get(nodeId) ?? [];
    if (childList.length === 0) return;

    const totalSubtree = childList.reduce((sum, c) => sum + (subtreeSize.get(c) ?? 1), 0);
    const availableAngle = endAngle - startAngle;

    let currentAngle = startAngle;
    for (const childId of childList) {
      const size = subtreeSize.get(childId) ?? 1;
      const span = (size / totalSubtree) * availableAngle;
      const angle = currentAngle + span / 2;
      const radius = currentRadius + 140;

      nodePositions.set(childId, {
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
      });

      // Recurse for child's subtree within its allocated sector
      assignPositions(childId, currentAngle, currentAngle + span, radius);
      currentAngle += span;
    }
  }

  // Seed radial sectors across the 360-degree circle around root
  assignPositions(root.id, -Math.PI, Math.PI, 0);

  // 5. Force-Directed Physics Relaxation (Fruchterman-Reingold)
  // Simulates spring forces along links + Coulomb repulsion between all nodes
  const iterations = 90;
  const kRepulsion = 160;
  const kSpring = 0.055;
  const idealDistance = 145;

  const simNodes = nodes.map((node) => {
    const init = nodePositions.get(node.id) ?? { x: 0, y: 0 };
    return {
      id: node.id,
      x: init.x,
      y: init.y,
      vx: 0,
      vy: 0,
      isRoot: node.id === root.id,
    };
  });

  const nodeIndexMap = new Map(simNodes.map((n, idx) => [n.id, idx]));

  // Build unique undirected links
  const undirectedLinks: Array<{ sourceIdx: number; targetIdx: number }> = [];
  const seenPairs = new Set<string>();
  for (const link of links) {
    if (!link.active || link.source === link.target) continue;
    const pair = [link.source, link.target].sort().join('--');
    if (!seenPairs.has(pair)) {
      seenPairs.add(pair);
      const sIdx = nodeIndexMap.get(link.source);
      const tIdx = nodeIndexMap.get(link.target);
      if (sIdx !== undefined && tIdx !== undefined) {
        undirectedLinks.push({ sourceIdx: sIdx, targetIdx: tIdx });
      }
    }
  }

  for (let iter = 0; iter < iterations; iter += 1) {
    const temperature = Math.max(0.12, 1 - iter / iterations);

    // Repulsion between all node pairs
    for (let i = 0; i < simNodes.length; i += 1) {
      for (let j = i + 1; j < simNodes.length; j += 1) {
        const u = simNodes[i];
        const v = simNodes[j];
        let dx = u.x - v.x;
        let dy = u.y - v.y;
        let dist = Math.hypot(dx, dy);
        if (dist < 1) {
          dx = (i + 1) * 3;
          dy = (j + 1) * 3;
          dist = Math.hypot(dx, dy);
        }
        const force = (kRepulsion * kRepulsion) / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;

        if (!u.isRoot) {
          u.vx += fx;
          u.vy += fy;
        }
        if (!v.isRoot) {
          v.vx -= fx;
          v.vy -= fy;
        }
      }
    }

    // Spring attraction along active links
    for (const link of undirectedLinks) {
      const u = simNodes[link.sourceIdx];
      const v = simNodes[link.targetIdx];
      const dx = v.x - u.x;
      const dy = v.y - u.y;
      const dist = Math.max(0.1, Math.hypot(dx, dy));
      const delta = dist - idealDistance;
      const force = delta * kSpring;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;

      if (!u.isRoot) {
        u.vx += fx;
        u.vy += fy;
      }
      if (!v.isRoot) {
        v.vx -= fx;
        v.vy -= fy;
      }
    }

    // Apply velocities with temperature damping
    for (const node of simNodes) {
      if (node.isRoot) {
        node.x = 0;
        node.y = 0;
        node.vx = 0;
        node.vy = 0;
        continue;
      }
      const speed = Math.hypot(node.vx, node.vy);
      const maxDisplacement = 28 * temperature;
      if (speed > 0) {
        const factor = Math.min(speed, maxDisplacement) / speed;
        node.x += node.vx * factor;
        node.y += node.vy * factor;
      }
      node.vx *= 0.52;
      node.vy *= 0.52;
    }
  }

  for (const node of simNodes) {
    points.set(node.id, { x: node.x, y: node.y });
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
    <div className="relative isolate z-0 h-[clamp(520px,68vh,900px)] min-h-[500px] mx-2 md:mx-3.5 overflow-hidden border border-[#e5e0d2] bg-[#f6f4eb] bg-[radial-gradient(#c4beae_1px,transparent_1px)] [background-size:24px_24px]" role="group" aria-label="Top-down positional mesh map">
      <Canvas
        className="block w-full h-full"
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
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-72 text-center pointer-events-none">
          <span className="inline-grid w-11 h-11 place-items-center rounded-full border border-[#c9cbb5] text-moss text-xl" aria-hidden="true">✳</span>
          <p className="mt-3 mb-1 text-forest font-serif text-lg">Listening for the first peer</p>
          <span className="text-muted text-xs">Nodes will appear here as they are discovered.</span>
        </div>
      )}
    </div>
  );
}
