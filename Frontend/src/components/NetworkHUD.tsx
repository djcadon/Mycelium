import React, { useState } from 'react';
import { AlertTriangle, Leaf, MapPin, Radio, Send } from 'lucide-react';
import type {
  MeshAlert,
  MeshNode,
  MeshLink,
  HopPacket,
  OutboundMessage,
} from '../types/mesh';
import type { BridgeConnectionStatus } from '../hooks/useMeshBridge';
import { MAX_PAYLOAD_BYTES } from '../services/bridgeProtocol';
import { NetworkMap } from './NetworkMap';

interface NetworkHUDProps {
  nodes: MeshNode[];
  links: MeshLink[];
  packets: HopPacket[];
  alerts: MeshAlert[];
  messages: OutboundMessage[];
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
  packets,
  alerts,
  messages,
  connectionStatus,
  lastEventAt,
  error,
  onSendMessage,
  selectedNode,
  selectedNodeId,
  onSelectNode,
}) => {
  const [draft, setDraft] = useState('');
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
  const reportedRssi = selectedLinks
    .map((link) => link.rssi)
    .filter((rssi): rssi is number => typeof rssi === 'number');
  const strongestRssi = reportedRssi.length ? Math.max(...reportedRssi) : null;
  const payloadBytes = new TextEncoder().encode(draft).length;
  const recentMessages = [...messages].slice(-5).reverse();
  const recentAlerts = [...alerts].slice(-5).reverse();
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
    : nodes.some((node) => node.positionDimensions === 3)
      ? '3D coordinates · top-down projection'
      : `${measuredCount} measured · ${estimatedCount} estimated · fitted to view`;

  const submitMessage = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedNode || !canSend) return;
    onSendMessage(selectedNode.id, draft);
    setDraft('');
  };

  return (
    <div className="app-frame">
      <header className="masthead">
        <a className="brand" href="/" aria-label="Mycelium home">
          <span className="brand-mark"><Leaf size={21} strokeWidth={1.7} /></span>
          <span>
            <span className="brand-kicker">Field systems · 01</span>
            <span className="brand-name">Mycelium</span>
          </span>
        </a>
        <div className="masthead-title">
          <span>Mesh observatory</span>
          <span className="masthead-divider" />
          <span>Live network</span>
        </div>
        <div className={`bridge-status connection-${connectionStatus}`}>
          <span className="status-dot" />
          <span>{connectionText}</span>
        </div>
      </header>

      <section className="metrics-strip" aria-label="Network summary">
        <div className="metric">
          <span className="metric-label">Peers online</span>
          <span className="metric-value">{onlinePeers.length}<small> / {peers.length}</small></span>
        </div>
        <div className="metric">
          <span className="metric-label">Active links</span>
          <span className="metric-value">{activeLinks}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Packets in flight</span>
          <span className="metric-value">{inFlightPackets.length}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Open alerts</span>
          <span className={`metric-value${openAlerts.length ? ' has-alert' : ''}`}>{openAlerts.length}</span>
        </div>
        <span className="strip-note">
          <Radio size={14} />
          {lastEventAt ? `Updated ${timeSince(lastEventAt)}` : 'Waiting for bridge events'}
        </span>
      </section>

      {error && (
        <div className="bridge-error" role="status" aria-live="polite">
          <AlertTriangle size={15} />
          <span>{error}</span>
        </div>
      )}

      <main className="workspace">
        <section className="map-panel" aria-labelledby="map-title">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Live field / network layer</span>
              <h1 id="map-title">The mesh</h1>
            </div>
            <span className="map-scale">{mapDescription}</span>
          </div>

          <NetworkMap
            nodes={nodes}
            links={links}
            packets={packets}
            alerts={alerts}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
          />

          <div className="map-legend">
            <span><i className="legend-dot healthy" /> Online</span>
            <span><i className="legend-dot sending" /> Transmitting</span>
            <span><i className="legend-dot alert" /> Alert</span>
            <span><i className="legend-dot offline" /> Offline</span>
            <span className="legend-note">Only bridge-reported links and packet hops are shown</span>
          </div>
        </section>

        <aside className="side-rail">
          <section className="rail-section">
            <div className="rail-heading">
              <span className="eyebrow">Selected peer</span>
              <span className="peer-count">{peers.length.toString().padStart(2, '0')}</span>
            </div>

            {selectedNode ? (
              <>
                <div className="peer-title">
                  <span className={`peer-status status-${selectedNode.status}`} aria-hidden="true" />
                  <div>
                    <h2>{selectedNode.name}</h2>
                    <p>{selectedNode.id}</p>
                  </div>
                </div>
                <dl className="peer-facts">
                  <div><dt>State</dt><dd>{selectedNode.status}</dd></div>
                  <div><dt>Hardware</dt><dd>{selectedNode.hardware}</dd></div>
                  <div><dt>Direct links</dt><dd>{selectedNode.neighborCount ?? '—'}</dd></div>
                  <div><dt>Strongest RSSI</dt><dd>{strongestRssi === null ? '—' : `${strongestRssi} dBm`}</dd></div>
                  <div><dt>Last seen</dt><dd>{timeSince(selectedNode.lastSeen)}</dd></div>
                  <div>
                    <dt>Position</dt>
                    <dd>
                      {selectedNode.positionSource === 'measured' || selectedNode.positionSource === 'estimated'
                        ? `${selectedNode.positionSource} · ${selectedCoordinates.map((value) => value.toFixed(1)).join(', ')}${selectedNode.positionUnits ? ` ${selectedNode.positionUnits}` : ''}`
                        : 'Diagrammatic only'}
                    </dd>
                  </div>
                  {selectedNode.positionConfidence !== undefined && (
                    <div><dt>Confidence</dt><dd>{Math.round(selectedNode.positionConfidence * 100)}%</dd></div>
                  )}
                </dl>
              </>
            ) : (
              <p className="empty-selection">Waiting for the bridge to report a peer.</p>
            )}
          </section>

          <section className="rail-section actions-section">
            <span className="eyebrow">Send to selected peer</span>
            <p className="section-copy">
              Bridge acceptance is not delivery. Success appears only after a destination ACK or echo.
            </p>
            <form onSubmit={submitMessage}>
              <textarea
                aria-label="Message payload"
                placeholder="Write a short message"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                rows={3}
                maxLength={MAX_PAYLOAD_BYTES}
              />
              <div className={`payload-count${payloadBytes > MAX_PAYLOAD_BYTES ? ' is-over-limit' : ''}`}>
                {payloadBytes} / {MAX_PAYLOAD_BYTES} bytes
              </div>
              <button
                className="action-button"
                type="submit"
                disabled={!canSend || payloadBytes === 0 || payloadBytes > MAX_PAYLOAD_BYTES}
              >
                <Send size={16} />
                <span>Send message</span>
              </button>
            </form>
            {!canSend && (
              <p className="form-hint">
                {connectionStatus !== 'connected'
                  ? 'Connect to the bridge to send.'
                  : selectedNode?.status === 'offline'
                    ? 'The selected peer is offline.'
                    : 'Select an online peer on the map.'}
              </p>
            )}
          </section>

          <section className="rail-section event-section">
            <div className="rail-heading">
              <span className="eyebrow">Message activity</span>
              {messages.length > 0 && <span className="peer-count">{messages.length.toString().padStart(2, '0')}</span>}
            </div>
            {recentMessages.length ? (
              <ul className="event-list" aria-live="polite">
                {recentMessages.map((message) => (
                  <li key={message.requestId}>
                    <div className="event-list-heading">
                      <strong>{messageStateLabel(message.status)}</strong>
                      <time>{formatTime(message.createdAt)}</time>
                    </div>
                    <span>To {message.targetId}: “{message.payload}”</span>
                    {message.response && <span className="event-response">{message.response}</span>}
                    {message.error && <span className="event-error">{message.error}</span>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-selection">Sent messages and destination responses will appear here.</p>
            )}
          </section>

          <section className="rail-section event-section">
            <div className="rail-heading">
              <span className="eyebrow">Network alerts</span>
              <span className={`peer-count${openAlerts.length ? ' has-alert' : ''}`}>
                {openAlerts.length.toString().padStart(2, '0')}
              </span>
            </div>
            {recentAlerts.length ? (
              <ul className="event-list">
                {recentAlerts.map((alert) => (
                  <li key={alert.id} className={alert.resolvedAt ? 'is-resolved' : ''}>
                    <div className="event-list-heading">
                      <strong>{alert.kind}{alert.resolvedAt ? ' · Resolved' : ' · Open'}</strong>
                      <time>{formatTime(alert.occurredAt)}</time>
                    </div>
                    <span>{alert.nodeId}{alert.message ? `: ${alert.message}` : ''}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-selection">No alert events reported by the bridge.</p>
            )}
          </section>

          <section className="rail-note">
            <MapPin size={17} />
            <p>Unmeasured peers use a diagrammatic layout. RSSI and topology alone do not establish physical coordinates.</p>
          </section>
        </aside>
      </main>

      <footer className="page-footer">
        <span>MYCELIUM / FIELD NOTES</span>
        <span>
          {lastEventAt
            ? `Last bridge event ${formatTime(lastEventAt)}`
            : 'No live events received yet'}
        </span>
      </footer>
    </div>
  );
};
