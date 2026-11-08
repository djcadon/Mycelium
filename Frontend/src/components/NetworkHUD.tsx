import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ChevronLeft, ChevronRight, Leaf, MapPin, Send, X } from 'lucide-react';
import type {
  MeshAlert,
  MeshNode,
  MeshLink,
  HopPacket,
  MeshRoute,
  OutboundMessage,
} from '../types/mesh';
import type { BridgeConnectionStatus } from '../hooks/useMeshBridge';
import { NetworkMap } from './NetworkMap';

interface NetworkHUDProps {
  nodes: MeshNode[];
  links: MeshLink[];
  routes: MeshRoute[];
  packets: HopPacket[];
  alerts: MeshAlert[];
  messages: OutboundMessage[];
  maxPayloadBytes: number;
  connectionStatus: BridgeConnectionStatus;
  lastEventAt: number | null;
  error: string | null;
  onSendMessage: (targetId: string, payload: string) => string;
  selectedNode: MeshNode | null;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function timeSince(timestamp: number): string {
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  return `${Math.floor(seconds / 60)}m ago`;
}

function messageStateLabel(status: OutboundMessage['status']): string {
  switch (status) {
    case 'sending': return 'Sending request';
    case 'queued': return 'Accepted by bridge';
    case 'forwarding': return 'Forwarding';
    case 'awaiting_ack': return 'Awaiting destination ACK';
    case 'unconfirmed': return 'Bridge response unconfirmed';
    case 'delivered': return 'Destination acknowledged';
    case 'echo_received': return 'Echo received';
    case 'failed': return 'Failed';
  }
}

export const NetworkHUD: React.FC<NetworkHUDProps> = ({
  nodes,
  links,
  routes,
  packets,
  alerts,
  messages,
  maxPayloadBytes,
  connectionStatus,
  lastEventAt,
  error,
  onSendMessage,
  selectedNode,
  selectedNodeId,
  onSelectNode,
}) => {
  const [draft, setDraft] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const handleSelectNode = (nodeId: string) => {
    onSelectNode(nodeId);
    setSidebarOpen(true);
  };

  useEffect(() => {
    if (selectedNodeId) {
      setSidebarOpen(true);
    }
  }, [selectedNodeId]);
  const peers = nodes.filter((node) => node.hardware !== 'Host Controller');
  const onlinePeers = peers.filter((node) => node.status !== 'offline');
  const activeLinks = useMemo(() => {
    const pairs = new Set<string>();
    for (const link of links) {
      if (link.active && link.source !== link.target) {
        pairs.add([link.source, link.target].sort().join('--'));
      }
    }
    return pairs.size;
  }, [links]);
  const inFlightPackets = packets.filter((packet) =>
    packet.status === 'forwarding' || packet.status === 'awaiting_ack',
  );
  const openAlerts = alerts.filter((alert) => alert.resolvedAt === undefined);
  const canSend = connectionStatus === 'connected' &&
    selectedNode !== null &&
    selectedNode.hardware !== 'Host Controller' &&
    selectedNode.status !== 'offline';
  const selectedLinks = selectedNode
    ? links.filter(
        (link) =>
          link.active &&
          (link.source === selectedNode.id || link.target === selectedNode.id),
      )
    : [];
  const directNeighbors = useMemo(() => {
    if (!selectedNode) return [];
    const neighborMap = new Map<string, MeshLink>();
    for (const link of links) {
      if (!link.active || link.source === link.target) continue;
      if (link.source === selectedNode.id) {
        neighborMap.set(link.target, link);
      } else if (link.target === selectedNode.id) {
        neighborMap.set(link.source, link);
      }
    }
    return Array.from(neighborMap.entries()).map(([neighborId, link]) => {
      const neighborNode = nodes.find((n) => n.id === neighborId);
      const isGateway = neighborId === 'Node-0000' ||
        neighborNode?.hardware.toLowerCase().includes('laptop') ||
        neighborNode?.hardware.toLowerCase().includes('bridge') ||
        neighborNode?.hardware.toLowerCase().includes('host');
      return {
        id: neighborId,
        name: isGateway ? `${neighborId} (Gateway)` : (neighborNode?.name ?? neighborId),
        status: neighborNode?.status ?? 'online',
        rssi: link.rssiMedian ?? link.rssi,
      };
    });
  }, [selectedNode, links, nodes]);
  const strongestLink = selectedLinks.reduce<MeshLink | null>((strongest, link) => {
    const level = link.rssiMedian ?? link.rssi;
    const strongestLevel = strongest?.rssiMedian ?? strongest?.rssi;
    return level !== undefined && (strongestLevel === undefined || level > strongestLevel)
      ? link
      : strongest;
  }, null);
  const strongestRssi = strongestLink?.rssiMedian ?? strongestLink?.rssi ?? null;
  const payloadBytes = new TextEncoder().encode(draft).length;
  const recentMessages = [...messages].slice(-15).reverse();
  const recentAlerts = [...alerts].slice(-15).reverse();
  const connectionText = {
    connecting: 'Connecting to bridge',
    connected: 'Bridge connected',
    reconnecting: 'Reconnecting to bridge',
    disconnected: 'Bridge disconnected',
  }[connectionStatus];
  const measuredCount = nodes.filter((node) => node.positionSource === 'measured').length;
  const estimatedCount = nodes.filter((node) => node.positionSource === 'estimated').length;
  const reportedPositionCount = measuredCount + estimatedCount;
  const selectedCoordinates = selectedNode
    ? selectedNode.positionDimensions === 2
      ? [selectedNode.position[0], selectedNode.position[2]]
      : selectedNode.position
    : [];
  const mapDescription = nodes.length === 0 || reportedPositionCount < nodes.length
    ? `Topology diagram · not to scale${reportedPositionCount ? ` · ${reportedPositionCount}/${nodes.length} coordinates` : ''}`
    : nodes.every((node) => node.positionDimensions === 3)
      ? '3D coordinates · top-down projection'
      : `${measuredCount} measured · ${estimatedCount} estimated · fitted to view`;

  const submitMessage = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedNode || !canSend) return;
    onSendMessage(selectedNode.id, draft);
    setDraft('');
  };

  return (
    <div className="w-full max-w-[1800px] mx-auto">
      {/* Masthead */}
      <header className="flex items-center justify-between min-h-[72px] border-b border-line">
        <a className="inline-flex items-center gap-3 text-ink no-underline" href="/" aria-label="Mycelium home">
          <span className="w-10 h-10 rounded-full border border-[#aeb39b] bg-[#e7e8d9] flex items-center justify-center text-moss">
            <Leaf size={20} strokeWidth={1.8} />
          </span>
          <span>
            <span className="block text-xs tracking-widest uppercase text-muted font-mono font-medium">Field systems · 01</span>
            <span className="block text-2xl font-serif text-forest tracking-tight font-normal">Mycelium</span>
          </span>
        </a>

        <div className={`flex items-center gap-2 px-3.5 py-1.5 rounded-full border text-sm font-mono font-medium ${
          connectionStatus === 'connected'
            ? 'bg-moss/10 border-moss/30 text-moss'
            : connectionStatus === 'disconnected'
              ? 'bg-clay/10 border-clay/30 text-clay'
              : 'bg-soil/10 border-soil/30 text-soil'
        }`}>
          <span className={`w-2 h-2 rounded-full ${
            connectionStatus === 'connected'
              ? 'bg-moss animate-pulse'
              : connectionStatus === 'disconnected'
                ? 'bg-clay'
                : 'bg-soil animate-ping'
          }`} />
          <span>{connectionText}</span>
        </div>
      </header>

      {/* Error Banner */}
      {error && (
        <div className="flex items-center gap-2.5 p-3 my-3 bg-[#f5e8df] border border-[#d5ab99] text-[#814a37] text-sm font-mono rounded" role="status" aria-live="polite">
          <AlertTriangle size={16} className="shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Main Workspace */}
      <main className="relative pt-4">
        <section className="relative isolate z-0 w-full overflow-hidden border border-line bg-paper shadow-sm rounded-none md:rounded" aria-labelledby="map-title">
          {/* Top-Left Floating Canvas Telemetry HUD */}
          <div className="absolute top-4 left-4 z-20 pointer-events-none flex flex-col gap-2">
            <div className="pointer-events-auto bg-[#faf8f0]/90 backdrop-blur-md border border-line rounded-lg px-5 py-3.5 shadow-md flex flex-col gap-3">
              <div className="flex items-center justify-between gap-6 pb-2.5 border-b border-line/60">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-moss animate-pulse" />
                  <span className="text-xs font-mono uppercase tracking-widest text-forest font-bold">Mesh Telemetry</span>
                </div>
                <span className="text-xs font-mono text-muted">{mapDescription}</span>
              </div>

              <div className="flex items-center gap-4 text-center">
                {/* Peers */}
                <div className="flex flex-col items-center min-w-16">
                  <span className="text-xs uppercase tracking-wider text-muted font-mono font-medium">Peers</span>
                  <span className="text-2xl font-serif font-bold text-forest leading-tight mt-0.5">
                    {onlinePeers.length}<span className="text-sm font-sans text-muted/70 font-normal ml-0.5">/{peers.length}</span>
                  </span>
                </div>

                {/* Active Links */}
                <div className="flex flex-col items-center min-w-16 border-l border-line/60 pl-4">
                  <span className="text-xs uppercase tracking-wider text-muted font-mono font-medium">Links</span>
                  <span className="text-2xl font-serif font-bold text-forest leading-tight mt-0.5">{activeLinks}</span>
                </div>

                {/* In Flight */}
                <div className="flex flex-col items-center min-w-16 border-l border-line/60 pl-4">
                  <span className="text-xs uppercase tracking-wider text-muted font-mono font-medium">In Flight</span>
                  <span className={`text-2xl font-serif font-bold leading-tight mt-0.5 ${inFlightPackets.length ? 'text-[#b28a49] animate-pulse' : 'text-forest'}`}>
                    {inFlightPackets.length}
                  </span>
                </div>

                {/* Alerts */}
                <div className="flex flex-col items-center min-w-16 border-l border-line/60 pl-4">
                  <span className="text-xs uppercase tracking-wider text-muted font-mono font-medium">Alerts</span>
                  <span className={`text-2xl font-serif font-bold leading-tight mt-0.5 ${openAlerts.length ? 'text-clay animate-bounce' : 'text-forest'}`}>
                    {openAlerts.length}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <NetworkMap
            nodes={nodes}
            links={links}
            packets={packets}
            alerts={alerts}
            selectedNodeId={selectedNodeId}
            onSelectNode={handleSelectNode}
          />

          <div className="flex flex-wrap items-center gap-5 px-5 py-3.5 text-sm text-soft-ink border-t border-line/60">
            <span className="inline-flex items-center gap-2"><i className="w-2.5 h-2.5 rounded-full bg-moss inline-block" /> Online</span>
            <span className="inline-flex items-center gap-2"><i className="w-2.5 h-2.5 rounded-full bg-[#b28a49] inline-block" /> Transmitting</span>
            <span className="inline-flex items-center gap-2"><i className="w-2.5 h-2.5 rounded-full bg-clay inline-block" /> Alert</span>
            <span className="inline-flex items-center gap-2"><i className="w-2.5 h-2.5 rounded-full bg-[#999487] inline-block" /> Offline</span>
            <span className="ml-auto text-xs text-muted font-mono hidden sm:inline">Drag to pan · scroll to zoom · click node to inspect · 20-byte BLE mesh</span>
          </div>
        </section>

        {/* Flush Edge-Attached Sidebar Overlay */}
        <aside className={`fixed inset-y-0 right-0 z-50 flex h-screen w-96 md:w-[420px] max-w-[92vw] flex-col bg-[#ebe9de]/95 backdrop-blur-xl border-l border-line shadow-2xl transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
          sidebarOpen ? 'translate-x-0' : 'translate-x-full'
        }`}>
          {/* Edge Toggle Pull-Tab */}
          <button
            className="absolute -left-8 top-24 w-8 h-14 flex flex-col items-center justify-center gap-1 bg-[#e4e2d2] hover:bg-[#dad7c4] text-forest border border-line border-r-0 rounded-l-md shadow-md cursor-pointer transition-colors z-50"
            type="button"
            onClick={() => setSidebarOpen((open) => !open)}
            aria-expanded={sidebarOpen}
            aria-label={sidebarOpen ? 'Collapse sidebar' : 'Open sidebar'}
            title={sidebarOpen ? 'Collapse sidebar' : 'Open sidebar'}
          >
            {sidebarOpen ? <ChevronRight size={18} strokeWidth={2.4} /> : <ChevronLeft size={18} strokeWidth={2.4} />}
            {openAlerts.length > 0 && (
              <span className="inline-flex items-center justify-center min-w-4 h-4 px-1 rounded-full bg-clay text-white text-xs font-bold leading-none">
                {openAlerts.length}
              </span>
            )}
          </button>

          {/* Sidebar Top Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-line bg-[#e2e0d4]/70 shrink-0">
            <div>
              <span className="block text-xs tracking-widest uppercase text-muted font-mono font-medium">Field Telemetry</span>
              <h2 className="text-xl font-serif text-forest font-medium mt-0.5">Mesh Inspector</h2>
            </div>
            <button
              className="w-8 h-8 flex items-center justify-center rounded text-soft-ink hover:text-forest hover:bg-black/5 transition-colors"
              type="button"
              onClick={() => setSidebarOpen(false)}
              aria-label="Collapse sidebar"
              title="Close sidebar"
            >
              <X size={18} />
            </button>
          </div>

          {/* Scrollable Sidebar Body */}
          <div className="flex-1 overflow-y-auto overscroll-contain pb-8 divide-y divide-line/60">
            {/* Section 1: Selected Peer */}
            <section className="p-5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs tracking-widest uppercase text-muted font-mono font-medium">Selected peer</span>
                {selectedNode && <span className="text-base font-serif text-muted font-normal">01</span>}
              </div>

              {selectedNode ? (
                <>
                  <div className="flex items-center gap-3 my-3">
                    <span className={`w-3.5 h-3.5 rounded-full shrink-0 border-2 ${
                      selectedNode.status === 'online'
                        ? 'bg-moss border-moss/30'
                        : selectedNode.status === 'alert'
                          ? 'bg-clay border-clay/30'
                          : selectedNode.status === 'transmitting'
                            ? 'bg-[#b28a49] border-[#b28a49]/30'
                            : 'bg-[#999487] border-[#999487]/30'
                    }`} />
                    <div>
                      <h2 className="text-xl font-serif text-forest font-medium leading-tight">{selectedNode.name}</h2>
                      <p className="text-xs text-muted font-mono mt-0.5">{selectedNode.id}</p>
                    </div>
                  </div>
                  <dl className="divide-y divide-line/40 text-sm mt-3">
                    <div className="flex justify-between py-2"><dt className="text-muted">State</dt><dd className="font-semibold text-ink capitalize">{selectedNode.status}</dd></div>
                    <div className="flex justify-between py-2"><dt className="text-muted">Hardware</dt><dd className="font-semibold text-ink">{selectedNode.hardware}</dd></div>
                    <div className="py-2.5">
                      <div className="flex justify-between items-baseline mb-1.5">
                        <dt className="text-muted">Direct links</dt>
                        <dd className="font-semibold text-ink">{directNeighbors.length}</dd>
                      </div>
                      {directNeighbors.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 mt-1.5">
                          {directNeighbors.map((neighbor) => (
                            <button
                              key={neighbor.id}
                              type="button"
                              onClick={() => handleSelectNode(neighbor.id)}
                              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-moss/30 bg-moss/10 hover:bg-moss/20 text-forest text-xs font-mono font-medium transition-colors text-left cursor-pointer"
                              title={`Click to inspect ${neighbor.id}`}
                            >
                              <span className={`w-2 h-2 rounded-full ${neighbor.status === 'alert' ? 'bg-clay' : 'bg-moss'}`} />
                              <span>{neighbor.name}</span>
                              {neighbor.rssi !== undefined && (
                                <span className="text-xs text-muted font-sans font-normal">({neighbor.rssi} dBm)</span>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="flex justify-between py-2"><dt className="text-muted">Strongest RSSI (30s median)</dt><dd className="font-semibold text-ink">{strongestRssi === null ? '—' : `${strongestRssi} dBm`}</dd></div>
                    <div className="flex justify-between py-2">
                      <dt className="text-muted">RSSI samples / MAD</dt>
                      <dd className="font-semibold text-ink">
                        {strongestLink?.sampleCount
                          ? `${strongestLink.sampleCount} / ${strongestLink.rssiMad === undefined ? 'warming up' : `${strongestLink.rssiMad.toFixed(1)} dB`}`
                          : '—'}
                      </dd>
                    </div>
                    <div className="flex justify-between py-2"><dt className="text-muted">Last seen</dt><dd className="font-semibold text-ink">{timeSince(selectedNode.lastSeen)}</dd></div>
                    <div className="flex justify-between py-2">
                      <dt className="text-muted">Position</dt>
                      <dd className="font-semibold text-ink text-right">
                        {selectedNode.positionSource === 'measured' || selectedNode.positionSource === 'estimated'
                          ? `${selectedNode.positionSource} · ${selectedCoordinates.map((value) => value.toFixed(1)).join(', ')}${selectedNode.positionUnits ? ` ${selectedNode.positionUnits}` : ''}`
                          : 'Diagrammatic only'}
                      </dd>
                    </div>
                    {selectedNode.positionConfidence !== undefined && (
                      <div className="flex justify-between py-2"><dt className="text-muted">Confidence</dt><dd className="font-semibold text-ink">{Math.round(selectedNode.positionConfidence * 100)}%</dd></div>
                    )}
                  </dl>
                </>
              ) : (
                <p className="text-sm text-muted leading-relaxed my-4">Click any node in the 3D scene to inspect its telemetry and send messages.</p>
              )}
            </section>

            {/* Section 2: Gateway Route Table */}
            <section className="p-5">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs tracking-widest uppercase text-muted font-mono font-medium">Gateway route table</span>
                <span className="text-base font-serif text-muted font-normal">
                  {routes.filter((route) => route.status === 'reachable').length.toString().padStart(2, '0')}
                </span>
              </div>
              <p className="text-xs text-muted mb-3 leading-snug">
                Routes discovered by the Gateway (Node-0000) to reach peers across the mesh.
              </p>
              {routes.some((route) => route.status === 'reachable') ? (
                <>
                  <ul className="max-h-[185px] overflow-y-auto space-y-2 pr-1 divide-y divide-line/30 text-sm">
                    {routes
                      .filter((route) => route.status === 'reachable')
                      .sort((left, right) => left.destination.localeCompare(right.destination))
                      .map((route) => {
                        const isRelayedBySelected = selectedNode && route.nextHop === selectedNode.id;
                        return (
                          <li key={route.destination} className="pt-2.5 first:pt-0">
                            <div className="flex justify-between items-baseline">
                              <strong className="text-forest font-semibold text-sm">{route.destination}</strong>
                              <span className="text-xs text-muted font-mono">{route.hopCount} {route.hopCount === 1 ? 'hop' : 'hops'}</span>
                            </div>
                            <div className="flex items-center justify-between mt-0.5">
                              <span className="text-muted text-xs">Next hop: {route.nextHop}</span>
                              {isRelayedBySelected && (
                                <span className="text-xs font-mono px-2 py-0.5 rounded bg-[#b28a49]/15 text-[#8c6b2d] font-semibold">
                                  Via selected peer
                                </span>
                              )}
                            </div>
                          </li>
                        );
                      })}
                  </ul>
                  <p className="text-xs text-muted mt-2">Hop count is routing cost, not physical distance.</p>
                </>
              ) : (
                <p className="text-sm text-muted my-3">Waiting for distributed route advertisements.</p>
              )}
            </section>

            {/* Section 3: Send to Selected Peer */}
            <section className="p-5">
              <span className="block text-xs tracking-widest uppercase text-muted font-mono font-medium mb-1">Send to selected peer</span>
              <p className="text-xs text-muted leading-relaxed mb-3">
                Bridge acceptance is not delivery. Success appears only after a destination ACK or echo.
              </p>
              <form onSubmit={submitMessage}>
                <textarea
                  className="w-full p-3 bg-paper/70 border border-line rounded text-sm text-ink placeholder:text-muted/60 focus:border-moss focus:outline-none focus:ring-1 focus:ring-moss resize-none"
                  aria-label="Message payload"
                  placeholder="Write a short message"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  rows={3}
                  maxLength={maxPayloadBytes}
                />
                <div className={`text-xs text-right font-mono mt-1 ${payloadBytes > maxPayloadBytes ? 'text-clay font-bold' : 'text-muted'}`}>
                  {payloadBytes} / {maxPayloadBytes} bytes
                </div>
                <button
                  className="w-full mt-2.5 py-2.5 px-4 bg-forest hover:bg-[#425b3c] disabled:bg-[#e2e0d4] disabled:text-[#989587] disabled:cursor-not-allowed text-paper font-semibold rounded flex items-center justify-center gap-2 text-sm transition-colors cursor-pointer"
                  type="submit"
                  disabled={!canSend || payloadBytes === 0 || payloadBytes > maxPayloadBytes}
                >
                  <Send size={16} />
                  <span>Send message</span>
                </button>
              </form>
              {!canSend && (
                <p className="text-xs text-muted mt-2">
                  {connectionStatus !== 'connected'
                    ? 'Connect to the bridge to send.'
                    : selectedNode?.status === 'offline'
                      ? 'The selected peer is offline.'
                      : 'Select an online peer on the map.'}
                </p>
              )}
            </section>

            {/* Section 4: Message Activity */}
            <section className="p-5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs tracking-widest uppercase text-muted font-mono font-medium">Message activity</span>
                {messages.length > 0 && <span className="text-base font-serif text-muted font-normal">{messages.length.toString().padStart(2, '0')}</span>}
              </div>
              {recentMessages.length ? (
                <ul className="max-h-[175px] overflow-y-auto space-y-2.5 pr-1 divide-y divide-line/30 text-sm" aria-live="polite">
                  {recentMessages.map((message) => (
                    <li key={message.requestId} className="pt-2.5 first:pt-0">
                      <div className="flex justify-between items-baseline">
                        <strong className="text-forest font-semibold text-sm">{messageStateLabel(message.status)}</strong>
                        <time className="text-xs text-muted font-mono">{formatTime(message.createdAt)}</time>
                      </div>
                      <span className="text-soft-ink block text-xs mt-0.5">To {message.targetId}: “{message.payload}”</span>
                      {message.response && <span className="text-moss font-semibold block text-xs mt-0.5">{message.response}</span>}
                      {message.error && <span className="text-clay font-semibold block text-xs mt-0.5">{message.error}</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted my-3">Sent messages and destination responses will appear here.</p>
              )}
            </section>

            {/* Section 5: Network Alerts */}
            <section className="p-5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs tracking-widest uppercase text-muted font-mono font-medium">Network alerts</span>
                <span className={`text-base font-serif font-normal ${openAlerts.length ? 'text-clay font-bold' : 'text-muted'}`}>
                  {openAlerts.length.toString().padStart(2, '0')}
                </span>
              </div>
              {recentAlerts.length ? (
                <ul className="max-h-[175px] overflow-y-auto space-y-2 pr-1 divide-y divide-line/30 text-sm">
                  {recentAlerts.map((alert) => (
                    <li key={alert.id} className={`pt-2.5 first:pt-0 ${alert.resolvedAt ? 'opacity-60' : ''}`}>
                      <div className="flex justify-between items-baseline">
                        <strong className={alert.resolvedAt ? 'text-muted font-semibold text-sm' : 'text-clay font-bold text-sm'}>
                          {alert.kind}{alert.resolvedAt ? ' · Resolved' : ' · Open'}
                        </strong>
                        <time className="text-xs text-muted font-mono">{formatTime(alert.occurredAt)}</time>
                      </div>
                      <span className="text-soft-ink block text-xs mt-0.5">{alert.nodeId}{alert.message ? `: ${alert.message}` : ''}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted my-3">No alert events reported by the bridge.</p>
              )}
            </section>

            {/* Section 6: Rail Note */}
            <section className="flex items-start gap-3 p-5 text-xs text-[#767b65] bg-black/[0.02]">
              <MapPin size={17} className="shrink-0 mt-0.5" />
              <p className="m-0 leading-relaxed">Unmeasured peers use a diagrammatic layout. RSSI and topology alone do not establish physical coordinates.</p>
            </section>
          </div>
        </aside>
      </main>

      {/* Footer */}
      <footer className="flex justify-between items-center gap-4 pt-4 text-xs font-mono text-[#8a8779]">
        <span className="text-soil uppercase font-medium">MYCELIUM / FIELD NOTES</span>
        <span>
          {lastEventAt
            ? `Last bridge event ${formatTime(lastEventAt)}`
            : 'No live events received yet'}
        </span>
      </footer>
    </div>
  );
};
