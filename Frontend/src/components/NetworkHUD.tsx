import React from 'react';
import { AlertTriangle, Zap, Power, Network, Wifi, Activity } from 'lucide-react';
import type { MeshNode, MeshLink, HopPacket } from '../types/mesh';

interface NetworkHUDProps {
  nodes: MeshNode[];
  links: MeshLink[];
  packets: HopPacket[];
  isLiveConnected: boolean;
  onTriggerPacket: (originId: string, targetId: string, type: 'HEARTBEAT' | 'SOS' | 'DATA') => void;
  onToggleNodeStatus: (nodeId: string) => void;
  selectedNode: MeshNode | null;
}

export const NetworkHUD: React.FC<NetworkHUDProps> = ({
  nodes,
  links,
  packets,
  isLiveConnected,
  onTriggerPacket,
  onToggleNodeStatus,
  selectedNode,
}) => {
  const onlinePeers = nodes.filter((n) => n.status !== 'offline');
  const activeLinks = links.filter((l) => l.active).length;
  const alertCount = nodes.filter((n) => n.status === 'alert').length;

  return (
    <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-6">
      {/* Top Header */}
      <div className="flex items-start justify-between">
        <div className="pointer-events-auto rounded-xl border border-slate-700/60 bg-slate-900/85 p-4 shadow-2xl backdrop-blur-md">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">
              <Network className="h-5 w-5 animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold tracking-wider text-slate-100 uppercase">
                  Mycelium <span className="text-emerald-400 font-mono text-sm">3D RF MESH</span>
                </h1>
                {isLiveConnected ? (
                  <span className="flex items-center gap-1 rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-300 border border-emerald-500/40 animate-pulse">
                    <Wifi className="h-3 w-3" /> LIVE RF BRIDGE ACTIVE
                  </span>
                ) : (
                  <span className="flex items-center gap-1 rounded-full bg-sky-500/20 px-2 py-0.5 text-[10px] font-semibold text-sky-300 border border-sky-500/30">
                    <Activity className="h-3 w-3" /> SIMULATION ENGINE
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400">
                Physics-based 3D Spatial Localization • Force-Directed Relaxation
              </p>
            </div>
          </div>

          {/* Metrics */}
          <div className="mt-4 grid grid-cols-4 gap-3 border-t border-slate-800 pt-3 text-center">
            <div>
              <span className="block text-xs text-slate-400">Peers Online</span>
              <span className="font-mono text-base font-bold text-slate-100">
                {onlinePeers.length}/{nodes.length}
              </span>
            </div>
            <div>
              <span className="block text-xs text-slate-400">P2P Links</span>
              <span className="font-mono text-base font-bold text-emerald-400">
                {activeLinks}
              </span>
            </div>
            <div>
              <span className="block text-xs text-slate-400">In-Flight</span>
              <span className="font-mono text-base font-bold text-sky-400">
                {packets.length}
              </span>
            </div>
            <div>
              <span className="block text-xs text-slate-400">Alerts</span>
              <span className={`font-mono text-base font-bold ${alertCount > 0 ? 'text-red-400 animate-bounce' : 'text-slate-400'}`}>
                {alertCount}
              </span>
            </div>
          </div>
        </div>

        {/* Action Controls */}
        <div className="pointer-events-auto flex flex-col gap-2 rounded-xl border border-slate-700/60 bg-slate-900/85 p-3 shadow-2xl backdrop-blur-md">
          <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400 px-1">
            Mesh Actions
          </span>
          <button
            onClick={() => {
              const ids = nodes.map(n => n.id);
              if (ids.length >= 2) onTriggerPacket(ids[0], ids[ids.length - 1], 'DATA');
            }}
            className="flex items-center gap-2 rounded-lg bg-emerald-500/20 px-3 py-2 text-xs font-semibold text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/30 transition cursor-pointer"
          >
            <Zap className="h-4 w-4" /> Broadcast Multi-Hop Packet
          </button>
          <button
            onClick={() => {
              const ids = nodes.map(n => n.id);
              if (ids.length >= 2) onTriggerPacket(ids[ids.length - 1], ids[0], 'SOS');
            }}
            className="flex items-center gap-2 rounded-lg bg-red-500/20 px-3 py-2 text-xs font-semibold text-red-300 border border-red-500/30 hover:bg-red-500/30 transition cursor-pointer"
          >
            <AlertTriangle className="h-4 w-4" /> Trigger Remote SOS Alert
          </button>
        </div>
      </div>

      {/* Zero Nodes Awaiting State */}
      {nodes.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="pointer-events-auto rounded-2xl border border-emerald-500/30 bg-slate-900/90 p-6 text-center shadow-2xl backdrop-blur-xl max-w-md">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 mb-3">
              <Wifi className="h-6 w-6 animate-pulse" />
            </div>
            <h3 className="font-mono text-sm font-bold uppercase tracking-wider text-slate-100">
              Scanning for BLE Mesh Nodes
            </h3>
            <p className="mt-2 text-xs text-slate-400 leading-relaxed">
              Wireless Bluetooth bridge listening over the air. Start a Mycelium node to watch it appear in 3D:
            </p>
            <code className="mt-3 block rounded-lg bg-slate-950 p-2.5 font-mono text-[11px] text-emerald-300 border border-slate-800 text-left">
              python3 ble.py listen
            </code>
            <p className="mt-2 text-[11px] text-slate-500">
              Zero USB cables required — pure over-the-air BLE discovery!
            </p>
          </div>
        </div>
      )}

      {/* Selected Peer Inspector */}
      {selectedNode && (
        <div className="pointer-events-auto flex justify-center">
          <div className="w-full max-w-xl rounded-xl border border-slate-700/80 bg-slate-900/95 p-4 shadow-2xl backdrop-blur-lg">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-3">
                <div className={`h-3 w-3 rounded-full ${selectedNode.status === 'online' ? 'bg-emerald-400 shadow-[0_0_8px_#10b981]' : selectedNode.status === 'alert' ? 'bg-red-500 shadow-[0_0_8px_#ef4444]' : 'bg-slate-500'}`} />
                <div>
                  <h3 className="font-mono text-sm font-bold text-slate-100">
                    {selectedNode.name} <span className="text-xs text-slate-400 font-normal">({selectedNode.ip})</span>
                  </h3>
                  <span className="text-xs text-emerald-400 uppercase font-semibold">
                    Equal Peer • {selectedNode.hardware}
                  </span>
                </div>
              </div>
              <button
                onClick={() => onToggleNodeStatus(selectedNode.id)}
                className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium border transition cursor-pointer ${
                  selectedNode.status === 'offline'
                    ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/30'
                    : 'bg-red-500/20 border-red-500/40 text-red-300 hover:bg-red-500/30'
                }`}
              >
                <Power className="h-3.5 w-3.5" />
                {selectedNode.status === 'offline' ? 'Power On Peer' : 'Simulate Node Drop'}
              </button>
            </div>

            <div className="mt-3 grid grid-cols-4 gap-3 text-center">
              <div className="rounded-lg bg-slate-800/50 p-2 border border-slate-800">
                <span className="text-[11px] text-slate-400 block">3D Coordinates</span>
                <span className="font-mono text-xs font-bold text-sky-400">
                  {selectedNode.position.map(n => Math.round(n)).join(', ')}
                </span>
              </div>
              <div className="rounded-lg bg-slate-800/50 p-2 border border-slate-800">
                <span className="text-[11px] text-slate-400 block">Battery</span>
                <span className="font-mono text-xs font-bold text-slate-200">{selectedNode.battery}%</span>
              </div>
              <div className="rounded-lg bg-slate-800/50 p-2 border border-slate-800">
                <span className="text-[11px] text-slate-400 block">Direct Neighbors</span>
                <span className="font-mono text-xs font-bold text-emerald-400">
                  {selectedNode.neighborCount}
                </span>
              </div>
              <div className="rounded-lg bg-slate-800/50 p-2 border border-slate-800">
                <span className="text-[11px] text-slate-400 block">RF Mode</span>
                <span className="font-mono text-xs font-bold text-amber-400">IBSS / Pure L2</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
