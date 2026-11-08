import React, { useState } from 'react';
import { AlertTriangle, ChevronLeft, ChevronRight, Leaf, MapPin, Radio, Send, X } from 'lucide-react';
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
  const peers = nodes.filter((node) => node.hardware !== 'Host Controller');
  const onlinePeers = peers.filter((node) => node.status !== 'offline');
  const activeLinks = links.filter((link) => link.active).length;
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
          <span className="w-9 h-9 rounded-full border border-[#aeb39b] bg-[#e7e8d9] flex items-center justify-center text-moss">
            <Leaf size={18} strokeWidth={1.8} />
          </span>
          <span>
            <span className="block text-[9px] tracking-widest uppercase text-muted font-mono">Field systems · 01</span>
            <span className="block text-xl font-serif text-forest tracking-tight font-normal">Mycelium</span>
          </span>
        </a>
        <div className="hidden sm:flex items-center gap-2 text-xs text-muted font-mono">
          <Radio size={13} className={connectionStatus === 'connected' ? 'text-moss animate-pulse' : 'text-muted'} />
          <span>{lastEventAt ? `Updated ${timeSince(lastEventAt)}` : 'Waiting for bridge events'}</span>
        </div>
        <div className={`flex items-center gap-2 px-3 py-1 rounded-full border text-xs font-mono font-medium ${
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
        <div className="flex items-center gap-2.5 p-3 my-3 bg-[#f5e8df] border border-[#d5ab99] text-[#814a37] text-xs font-mono rounded" role="status" aria-live="polite">
          <AlertTriangle size={15} className="shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Main Workspace */}
      <main className="relative pt-4">
        <section className="relative w-full overflow-hidden border border-line bg-paper shadow-sm rounded-none md:rounded" aria-labelledby="map-title">
          {/* Top-Left Floating Canvas Telemetry HUD */}
          <div className="absolute top-4 left-4 z-20 pointer-events-none flex flex-col gap-2">
            <div className="pointer-events-auto bg-[#faf8f0]/90 backdrop-blur-md border border-line rounded-lg px-4 py-3 shadow-md flex flex-col gap-2.5">
              <div className="flex items-center justify-between gap-6 pb-2 border-b border-line/60">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-moss animate-pulse" />
                  <span className="text-[10px] font-mono uppercase tracking-widest text-forest font-bold">Mesh Telemetry</span>
                </div>
                <span className="text-[10px] font-mono text-muted">{mapDescription}</span>
              </div>

              <div className="flex items-center gap-3.5 text-center">
                {/* Peers */}
                <div className="flex flex-col items-center min-w-[54px]">
                  <span className="text-[9px] uppercase tracking-wider text-muted font-mono">Peers</span>
                  <span className="text-lg font-serif font-bold text-forest leading-tight mt-0.5">
                    {onlinePeers.length}<span className="text-xs font-sans text-muted/70 font-normal ml-0.5">/{peers.length}</span>
                  </span>
                </div>

                {/* Active Links */}
                <div className="flex flex-col items-center min-w-[54px] border-l border-line/60 pl-3.5">
                  <span className="text-[9px] uppercase tracking-wider text-muted font-mono">Links</span>
                  <span className="text-lg font-serif font-bold text-forest leading-tight mt-0.5">{activeLinks}</span>
                </div>

                {/* In Flight */}
                <div className="flex flex-col items-center min-w-[54px] border-l border-line/60 pl-3.5">
                  <span className="text-[9px] uppercase tracking-wider text-muted font-mono">In Flight</span>
                  <span className={`text-lg font-serif font-bold leading-tight mt-0.5 ${inFlightPackets.length ? 'text-[#b28a49] animate-pulse' : 'text-forest'}`}>
                    {inFlightPackets.length}
                  </span>
                </div>

                {/* Alerts */}
                <div className="flex flex-col items-center min-w-[54px] border-l border-line/60 pl-3.5">
                  <span className="text-[9px] uppercase tracking-wider text-muted font-mono">Alerts</span>
                  <span className={`text-lg font-serif font-bold leading-tight mt-0.5 ${openAlerts.length ? 'text-clay animate-bounce' : 'text-forest'}`}>
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
            onSelectNode={onSelectNode}
          />

          <div className="flex flex-wrap items-center gap-4 px-5 py-3 text-xs text-soft-ink border-t border-line/60">
            <span className="inline-flex items-center gap-1.5"><i className="w-2 h-2 rounded-full bg-moss inline-block" /> Online</span>
            <span className="inline-flex items-center gap-1.5"><i className="w-2 h-2 rounded-full bg-[#b28a49] inline-block" /> Transmitting</span>
            <span className="inline-flex items-center gap-1.5"><i className="w-2 h-2 rounded-full bg-clay inline-block" /> Alert</span>
            <span className="inline-flex items-center gap-1.5"><i className="w-2 h-2 rounded-full bg-[#999487] inline-block" /> Offline</span>
            <span className="ml-auto text-[11px] text-muted font-mono hidden sm:inline">Drag to pan · scroll to zoom · click node to inspect · 20-byte BLE mesh</span>
          </div>
        </section>

        {/* Flush Edge-Attached Sidebar Overlay */}
        <aside className={`fixed inset-y-0 right-0 z-50 flex h-screen w-[380px] max-w-[92vw] flex-col bg-[#ebe9de]/95 backdrop-blur-xl border-l border-line shadow-2xl transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
          sidebarOpen ? 'translate-x-0' : 'translate-x-full'
        }`}>
          {/* Edge Toggle Pull-Tab */}
          <button
            className="absolute -left-8 top-24 w-8 h-13 flex flex-col items-center justify-center gap-1 bg-[#e4e2d2] hover:bg-[#dad7c4] text-forest border border-line border-r-0 rounded-l-md shadow-md cursor-pointer transition-colors z-50"
            type="button"
            onClick={() => setSidebarOpen((open) => !open)}
            aria-expanded={sidebarOpen}
            aria-label={sidebarOpen ? 'Collapse sidebar' : 'Open sidebar'}
            title={sidebarOpen ? 'Collapse sidebar' : 'Open sidebar'}
          >
            {sidebarOpen ? <ChevronRight size={17} strokeWidth={2.4} /> : <ChevronLeft size={17} strokeWidth={2.4} />}
            {openAlerts.length > 0 && (
              <span className="inline-flex items-center justify-center min-w-[15px] h-[15px] px-1 rounded-full bg-clay text-white text-[8px] font-bold leading-none">
                {openAlerts.length}
              </span>
            )}
          </button>

          {/* Sidebar Top Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-line bg-[#e2e0d4]/70 shrink-0">
            <div>
              <span className="block text-[10px] tracking-widest uppercase text-muted font-mono">Field Telemetry</span>
              <h2 className="text-lg font-serif text-forest font-normal mt-0.5">Mesh Inspector</h2>
            </div>
            <button
              className="w-7 h-7 flex items-center justify-center rounded text-soft-ink hover:text-forest hover:bg-black/5 transition-colors"
              type="button"
              onClick={() => setSidebarOpen(false)}
              aria-label="Collapse sidebar"
              title="Close sidebar"
            >
              <X size={16} />
            </button>
          </div>

          {/* Scrollable Sidebar Body */}
          <div className="flex-1 overflow-y-auto overscroll-contain pb-8 divide-y divide-line/60">
            {/* Section 1: Selected Peer */}
            <section className="p-5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] tracking-widest uppercase text-muted font-mono">Selected peer</span>
                {selectedNode && <span className="text-sm font-serif text-muted font-normal">01</span>}
              </div>

              {selectedNode ? (
                <>
                  <div className="flex items-center gap-3 my-3">
                    <span className={`w-3 h-3 rounded-full shrink-0 border-2 ${
                      selectedNode.status === 'online'
                        ? 'bg-moss border-moss/30'
                        : selectedNode.status === 'alert'
                          ? 'bg-clay border-clay/30'
                          : selectedNode.status === 'transmitting'
                            ? 'bg-[#b28a49] border-[#b28a49]/30'
                            : 'bg-[#999487] border-[#999487]/30'
                    }`} />
                    <div>
                      <h2 className="text-lg font-serif text-forest font-normal leading-tight">{selectedNode.name}</h2>
                      <p className="text-[10px] text-muted font-mono">{selectedNode.id}</p>
                    </div>
                  </div>
                  <dl className="divide-y divide-line/40 text-xs mt-2">
                    <div className="flex justify-between py-1.5"><dt className="text-muted">State</dt><dd className="font-semibold text-ink capitalize">{selectedNode.status}</dd></div>
                    <div className="flex justify-between py-1.5"><dt className="text-muted">Hardware</dt><dd className="font-semibold text-ink">{selectedNode.hardware}</dd></div>
                    <div className="flex justify-between py-1.5"><dt className="text-muted">Direct links</dt><dd className="font-semibold text-ink">{selectedNode.neighborCount ?? '—'}</dd></div>
                    <div className="flex justify-between py-1.5"><dt className="text-muted">Strongest RSSI (30s median)</dt><dd className="font-semibold text-ink">{strongestRssi === null ? '—' : `${strongestRssi} dBm`}</dd></div>
                    <div className="flex justify-between py-1.5">
                      <dt className="text-muted">RSSI samples / MAD</dt>
                      <dd className="font-semibold text-ink">
                        {strongestLink?.sampleCount
                          ? `${strongestLink.sampleCount} / ${strongestLink.rssiMad === undefined ? 'warming up' : `${strongestLink.rssiMad.toFixed(1)} dB`}`
                          : '—'}
                      </dd>
                    </div>
                    <div className="flex justify-between py-1.5"><dt className="text-muted">Last seen</dt><dd className="font-semibold text-ink">{timeSince(selectedNode.lastSeen)}</dd></div>
                    <div className="flex justify-between py-1.5">
                      <dt className="text-muted">Position</dt>
                      <dd className="font-semibold text-ink text-right">
                        {selectedNode.positionSource === 'measured' || selectedNode.positionSource === 'estimated'
                          ? `${selectedNode.positionSource} · ${selectedCoordinates.map((value) => value.toFixed(1)).join(', ')}${selectedNode.positionUnits ? ` ${selectedNode.positionUnits}` : ''}`
                          : 'Diagrammatic only'}
                      </dd>
                    </div>
                    {selectedNode.positionConfidence !== undefined && (
                      <div className="flex justify-between py-1.5"><dt className="text-muted">Confidence</dt><dd className="font-semibold text-ink">{Math.round(selectedNode.positionConfidence * 100)}%</dd></div>
                    )}
                  </dl>
                </>
              ) : (
                <p className="text-xs text-muted leading-relaxed my-4">Click any node in the 3D scene to inspect its telemetry and send messages.</p>
              )}
            </section>

            {/* Section 2: Gateway Route Table */}
            <section className="p-5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] tracking-widest uppercase text-muted font-mono">Gateway route table</span>
                <span className="text-sm font-serif text-muted font-normal">
                  {routes.filter((route) => route.status === 'reachable').length.toString().padStart(2, '0')}
                </span>
              </div>
              {routes.some((route) => route.status === 'reachable') ? (
                <>
                  <ul className="max-h-[165px] overflow-y-auto space-y-2 pr-1 divide-y divide-line/30 text-xs">
                    {routes
                      .filter((route) => route.status === 'reachable')
                      .sort((left, right) => left.destination.localeCompare(right.destination))
                      .map((route) => (
                        <li key={route.destination} className="pt-2 first:pt-0">
                          <div className="flex justify-between items-baseline">
                            <strong className="text-forest font-semibold">{route.destination}</strong>
                            <span className="text-[10px] text-muted font-mono">{route.hopCount} {route.hopCount === 1 ? 'hop' : 'hops'}</span>
                          </div>
                          <span className="text-muted text-[11px] block mt-0.5">Next hop: {route.nextHop}</span>
                        </li>
                      ))}
                  </ul>
                  <p className="text-[11px] text-muted mt-2">Hop count is routing cost, not physical distance.</p>
                </>
              ) : (
                <p className="text-xs text-muted my-3">Waiting for distributed route advertisements.</p>
              )}
            </section>

            {/* Section 3: Send to Selected Peer */}
            <section className="p-5">
              <span className="block text-[10px] tracking-widest uppercase text-muted font-mono mb-1">Send to selected peer</span>
              <p className="text-[11px] text-muted leading-relaxed mb-3">
                Bridge acceptance is not delivery. Success appears only after a destination ACK or echo.
              </p>
              <form onSubmit={submitMessage}>
                <textarea
                  className="w-full p-2.5 bg-paper/70 border border-line rounded text-xs text-ink placeholder:text-muted/60 focus:border-moss focus:outline-none focus:ring-1 focus:ring-moss resize-none"
                  aria-label="Message payload"
                  placeholder="Write a short message"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  rows={3}
                  maxLength={maxPayloadBytes}
                />
                <div className={`text-[10px] text-right font-mono mt-1 ${payloadBytes > maxPayloadBytes ? 'text-clay font-bold' : 'text-muted'}`}>
                  {payloadBytes} / {maxPayloadBytes} bytes
                </div>
                <button
                  className="w-full mt-2 py-2 px-3 bg-forest hover:bg-[#425b3c] disabled:bg-[#e2e0d4] disabled:text-[#989587] disabled:cursor-not-allowed text-paper font-medium rounded flex items-center justify-center gap-2 text-xs transition-colors"
                  type="submit"
                  disabled={!canSend || payloadBytes === 0 || payloadBytes > maxPayloadBytes}
                >
                  <Send size={15} />
                  <span>Send message</span>
                </button>
              </form>
              {!canSend && (
                <p className="text-[11px] text-muted mt-2">
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
                <span className="text-[10px] tracking-widest uppercase text-muted font-mono">Message activity</span>
                {messages.length > 0 && <span className="text-sm font-serif text-muted font-normal">{messages.length.toString().padStart(2, '0')}</span>}
              </div>
              {recentMessages.length ? (
                <ul className="max-h-[165px] overflow-y-auto space-y-2.5 pr-1 divide-y divide-line/30 text-xs" aria-live="polite">
                  {recentMessages.map((message) => (
                    <li key={message.requestId} className="pt-2 first:pt-0">
                      <div className="flex justify-between items-baseline">
                        <strong className="text-forest font-semibold">{messageStateLabel(message.status)}</strong>
                        <time className="text-[9px] text-muted font-mono">{formatTime(message.createdAt)}</time>
                      </div>
                      <span className="text-soft-ink block text-[11px] mt-0.5">To {message.targetId}: “{message.payload}”</span>
                      {message.response && <span className="text-moss font-semibold block text-[11px] mt-0.5">{message.response}</span>}
                      {message.error && <span className="text-clay font-semibold block text-[11px] mt-0.5">{message.error}</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted my-3">Sent messages and destination responses will appear here.</p>
              )}
            </section>

            {/* Section 5: Network Alerts */}
            <section className="p-5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] tracking-widest uppercase text-muted font-mono">Network alerts</span>
                <span className={`text-sm font-serif font-normal ${openAlerts.length ? 'text-clay font-bold' : 'text-muted'}`}>
                  {openAlerts.length.toString().padStart(2, '0')}
                </span>
              </div>
              {recentAlerts.length ? (
                <ul className="max-h-[165px] overflow-y-auto space-y-2 pr-1 divide-y divide-line/30 text-xs">
                  {recentAlerts.map((alert) => (
                    <li key={alert.id} className={`pt-2 first:pt-0 ${alert.resolvedAt ? 'opacity-60' : ''}`}>
                      <div className="flex justify-between items-baseline">
                        <strong className={alert.resolvedAt ? 'text-muted font-semibold' : 'text-clay font-bold'}>
                          {alert.kind}{alert.resolvedAt ? ' · Resolved' : ' · Open'}
                        </strong>
                        <time className="text-[9px] text-muted font-mono">{formatTime(alert.occurredAt)}</time>
                      </div>
                      <span className="text-soft-ink block text-[11px] mt-0.5">{alert.nodeId}{alert.message ? `: ${alert.message}` : ''}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted my-3">No alert events reported by the bridge.</p>
              )}
            </section>

            {/* Section 6: Rail Note */}
            <section className="flex items-start gap-2.5 p-5 text-[11px] text-[#767b65] bg-black/[0.02]">
              <MapPin size={16} className="shrink-0 mt-0.5" />
              <p className="m-0 leading-relaxed">Unmeasured peers use a diagrammatic layout. RSSI and topology alone do not establish physical coordinates.</p>
            </section>
          </div>
        </aside>
      </main>

      {/* Footer */}
      <footer className="flex justify-between items-center gap-4 pt-4 text-[9px] font-mono text-[#8a8779]">
        <span className="text-soil uppercase">MYCELIUM / FIELD NOTES</span>
        <span>
          {lastEventAt
            ? `Last bridge event ${formatTime(lastEventAt)}`
            : 'No live events received yet'}
        </span>
      </footer>
    </div>
  );
};
