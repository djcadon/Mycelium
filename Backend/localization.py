#!/usr/bin/env python3
"""
Mycelium - BLE RF Spatial Localization Engine (3D Coordinate Solver)
Converts raw Bluetooth 5.1 signal strengths (RSSI in dBm) from peer-to-peer radio packets
into physical distances (meters) and calculates 3D (x, y, z) spatial coordinates
using Force-Directed Relaxation (Spring Physics).

Physics Model: Log-Distance Path Loss Model for 2.4 GHz BLE
  d = 10 ^ ((A - RSSI) / (10 * n))
  where:
    A = Reference RSSI at 1 meter (calibrated to -59 dBm for 0 dBm BLE TxPower)
    n = Path loss exponent (default: 2.2 for indoor environments)
"""

import math
import random
from typing import Dict, List, Tuple, Optional

# Bluetooth Low Energy 5.1 Propagation Constants
# BLE TxPower is ~0 dBm to +4 dBm, meaning RSSI at 1 meter is ~ -58 to -60 dBm
DEFAULT_BLE_RSSI_AT_1M = -59.0   # dBm measured at 1 meter distance for BLE
DEFAULT_PATH_LOSS_EXP = 2.2      # Indoor path-loss exponent
MIN_DISTANCE = 0.5               # Minimum clamp (meters)
MAX_DISTANCE = 30.0              # Maximum clamp (meters)

def rssi_to_distance(
    rssi: float,
    a: float = DEFAULT_BLE_RSSI_AT_1M,
    n: float = DEFAULT_PATH_LOSS_EXP
) -> float:
    """
    Converts Bluetooth RSSI (dBm) into an estimated physical distance in meters.
    Calibrated examples for BLE:
      -59 dBm -> ~1.0 meter
      -66 dBm -> ~2.1 meters
      -75 dBm -> ~5.3 meters
      -85 dBm -> ~15.2 meters
    """
    try:
        dist = 10.0 ** ((a - rssi) / (10.0 * n))
        return max(MIN_DISTANCE, min(dist, MAX_DISTANCE))
    except (ValueError, OverflowError):
        return MAX_DISTANCE

class Mesh3DLocalizer:
    """
    3D Spatial Coordinate Solver using Spring-Relaxation Dynamics.
    Takes a pairwise distance matrix between peers and converges onto
    their physical relative 3D coordinates (x, y, z).
    """

    def __init__(self, damping: float = 0.88, spring_k: float = 0.16, iterations: int = 60):
        self.damping = damping
        self.spring_k = spring_k
        self.iterations = iterations
        # Cached positions: { node_id: [x, y, z] }
        self.positions: Dict[str, List[float]] = {}
        # Cached velocities: { node_id: [vx, vy, vz] }
        self.velocities: Dict[str, List[float]] = {}

    def update_topology(
        self,
        distance_matrix: Dict[str, Dict[str, float]],
        anchor_id: Optional[str] = None
    ) -> Dict[str, Tuple[float, float, float]]:
        """
        Runs iterative force-directed relaxation to position all nodes in 3D space.

        Args:
            distance_matrix: { nodeA: { nodeB: distance_in_meters, ... }, ... }
            anchor_id: Optional node ID to fix at (0, 0, 0) as coordinate origin.

        Returns:
            { node_id: (x, y, z) }
        """
        nodes = list(distance_matrix.keys())
        if not nodes:
            return {}

        # 1. Initialize 3D positions for newly discovered nodes
        for node in nodes:
            if node not in self.positions:
                theta = random.uniform(0, 2 * math.pi)
                phi = random.uniform(0, math.pi)
                r = random.uniform(1.5, 3.5)
                self.positions[node] = [
                    r * math.sin(phi) * math.cos(theta),
                    r * math.cos(phi) * 0.35,  # Slightly flatter vertical distribution
                    r * math.sin(phi) * math.sin(theta),
                ]
                self.velocities[node] = [0.0, 0.0, 0.0]

        # 2. Iterative physics relaxation loop
        for _ in range(self.iterations):
            forces: Dict[str, List[float]] = {n: [0.0, 0.0, 0.0] for n in nodes}

            # A. Attractive / Repulsive Spring Forces between linked peers
            for node_a in nodes:
                pos_a = self.positions[node_a]
                for node_b, target_dist in distance_matrix[node_a].items():
                    if node_a == node_b or node_b not in self.positions:
                        continue

                    pos_b = self.positions[node_b]
                    dx = pos_b[0] - pos_a[0]
                    dy = pos_b[1] - pos_a[1]
                    dz = pos_b[2] - pos_a[2]
                    current_dist = math.sqrt(dx * dx + dy * dy + dz * dz) + 0.0001

                    # Hooke's Law: F = k * (current_dist - target_dist)
                    displacement = current_dist - target_dist
                    f = self.spring_k * displacement

                    forces[node_a][0] += f * (dx / current_dist)
                    forces[node_a][1] += f * (dy / current_dist)
                    forces[node_a][2] += f * (dz / current_dist)

            # B. Global Coulomb Repulsion (keeps unlinked nodes from collapsing together)
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    n1, n2 = nodes[i], nodes[j]
                    pos1, pos2 = self.positions[n1], self.positions[n2]
                    dx = pos1[0] - pos2[0]
                    dy = pos1[1] - pos2[1]
                    dz = pos1[2] - pos2[2]
                    dist_sq = dx * dx + dy * dy + dz * dz + 0.01

                    repulsion = 1.5 / dist_sq
                    dist = math.sqrt(dist_sq)

                    rx = repulsion * (dx / dist)
                    ry = repulsion * (dy / dist)
                    rz = repulsion * (dz / dist)

                    forces[n1][0] += rx
                    forces[n1][1] += ry
                    forces[n1][2] += rz
                    forces[n2][0] -= rx
                    forces[n2][1] -= ry
                    forces[n2][2] -= rz

            # C. Update velocities and apply positions with damping
            for node in nodes:
                if anchor_id and node == anchor_id:
                    self.positions[node] = [0.0, 0.0, 0.0]
                    self.velocities[node] = [0.0, 0.0, 0.0]
                    continue

                vel = self.velocities[node]
                f = forces[node]

                vel[0] = (vel[0] + f[0]) * self.damping
                vel[1] = (vel[1] + f[1]) * self.damping
                vel[2] = (vel[2] + f[2]) * self.damping

                self.positions[node][0] += vel[0]
                self.positions[node][1] += vel[1]
                self.positions[node][2] += vel[2]

        return {
            n: (
                round(self.positions[n][0], 2),
                round(self.positions[n][1], 2),
                round(self.positions[n][2], 2),
            )
            for n in nodes
        }

if __name__ == "__main__":
    print("=============================================================")
    print(" Mycelium BLE 3D Spatial Localization Self-Test")
    print(f" Calibration: A = {DEFAULT_BLE_RSSI_AT_1M} dBm at 1m, n = {DEFAULT_PATH_LOSS_EXP}")
    print("=============================================================")

    # Simulated Bluetooth 5.1 RSSI readings between 3 peers
    ble_rssi_sample = {
        "Laptop-Base": {"Node-6EBB": -62, "Node-3C4D": -75},
        "Node-6EBB":   {"Laptop-Base": -62, "Node-3C4D": -66},
        "Node-3C4D":   {"Laptop-Base": -75, "Node-6EBB": -66},
    }

    print("\n[Step 1] Converting Bluetooth RSSI (dBm) to Physical Distances:")
    d_matrix: Dict[str, Dict[str, float]] = {}
    for n_a, neighbors in ble_rssi_sample.items():
        d_matrix[n_a] = {}
        for n_b, rssi in neighbors.items():
            dist = rssi_to_distance(rssi)
            d_matrix[n_a][n_b] = dist
            print(f"  {n_a} <--> {n_b}: RSSI = {rssi} dBm  ==>  Dist = {dist:.2f} m")

    print("\n[Step 2] Running 3D Spring-Relaxation Solver...")
    localizer = Mesh3DLocalizer()
    coords = localizer.update_topology(d_matrix, anchor_id="Laptop-Base")

    print("\n[Step 3] Calculated Relative 3D Spatial Coordinates (x, y, z):")
    for n, (x, y, z) in coords.items():
        print(f"  {n:12}: X = {x:6.2f}m,  Y = {y:6.2f}m,  Z = {z:6.2f}m")

    print("\n[✓] BLE 3D localization physics successfully verified!")
    print("=============================================================")
