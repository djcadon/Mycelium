import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  MeshAlert,
  MeshLink,
  MeshNode,
  MeshRoute,
  HopPacket,
  OutboundMessage,
} from '../types/mesh';
import {
  MAX_PAYLOAD_BYTES,
  parseBridgeEvent,
  type SendMessageRequest,
} from '../services/bridgeProtocol';

export type BridgeConnectionStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

interface MeshBridgeState {
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
  sendMessage: (targetId: string, payload: string) => string;
}

const BRIDGE_URL = 'ws://localhost:8765';
const MAX_HISTORY = 100;
const REQUEST_TIMEOUT_MS = 8000;

function updateMessage(
  messages: OutboundMessage[],
  requestId: string | undefined,
  messageId: string | undefined,
  update: (message: OutboundMessage) => OutboundMessage,
): OutboundMessage[] {
  return messages.map((message) =>
    (requestId && message.requestId === requestId) ||
    (messageId && message.messageId === messageId)
      ? update(message)
      : message,
  );
}

export function useMeshBridge(): MeshBridgeState {
  const [nodes, setNodes] = useState<MeshNode[]>([]);
  const [links, setLinks] = useState<MeshLink[]>([]);
  const [routes, setRoutes] = useState<MeshRoute[]>([]);
  const [packets, setPackets] = useState<HopPacket[]>([]);
  const [alerts, setAlerts] = useState<MeshAlert[]>([]);
  const [messages, setMessages] = useState<OutboundMessage[]>([]);
  const [maxPayloadBytes, setMaxPayloadBytes] = useState(MAX_PAYLOAD_BYTES);
  const [connectionStatus, setConnectionStatus] =
    useState<BridgeConnectionStatus>('connecting');
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const requestTimersRef = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const clearRequestTimer = useCallback((requestId: string) => {
    const timer = requestTimersRef.current.get(requestId);
    if (timer) clearTimeout(timer);
    requestTimersRef.current.delete(requestId);
  }, []);

  const handleEvent = useCallback((rawEvent: unknown) => {
    const event = parseBridgeEvent(rawEvent);
    setLastEventAt(Date.now());
    setError(null);

    switch (event.type) {
      case 'BRIDGE_CAPABILITIES':
        setMaxPayloadBytes(event.maxPayloadBytes);
        break;
      case 'STATE_SNAPSHOT':
        setNodes(event.nodes);
        setLinks(event.links);
        setRoutes(event.routes);
        setAlerts(event.alerts.slice(-MAX_HISTORY));
        setPackets(event.packets.slice(-MAX_HISTORY));
        break;
      case 'TOPOLOGY_UPDATE':
        setNodes(event.nodes);
        setLinks(event.links);
        break;
      case 'ROUTE_UPDATE':
        setRoutes(event.routes);
        break;
      case 'PEER_UPDATE':
        setNodes((current) => {
          const exists = current.some((node) => node.id === event.node.id);
          return exists
            ? current.map((node) => node.id === event.node.id ? event.node : node)
            : [...current, event.node];
        });
        break;
      case 'ALERT_RAISED':
        setAlerts((current) => [
          ...current.filter((alert) => alert.id !== event.alert.id),
          event.alert,
        ].slice(-MAX_HISTORY));
        break;
      case 'ALERT_CLEARED':
        setAlerts((current) => current.map((alert) =>
          alert.id === event.alertId ? { ...alert, resolvedAt: event.occurredAt } : alert,
        ));
        break;
      case 'PACKET_STATE':
        if (event.packet.requestId) clearRequestTimer(event.packet.requestId);
        setPackets((current) => [
          ...current.filter((packet) => packet.id !== event.packet.id),
          event.packet,
        ].slice(-MAX_HISTORY));
        if (event.packet.requestId || event.packet.id) {
          setMessages((current) => updateMessage(
            current,
            event.packet.requestId,
            event.packet.id,
            (message) => ({
              ...message,
              status: event.packet.status === 'failed'
                ? 'failed'
                : event.packet.status === 'forwarding'
                  ? 'forwarding'
                  : event.packet.status === 'awaiting_ack'
                    ? 'awaiting_ack'
                    : 'queued',
              error: event.packet.failureReason ?? message.error,
            }),
          ));
        }
        break;
      case 'SEND_ACCEPTED':
        clearRequestTimer(event.requestId);
        setMessages((current) => updateMessage(
          current,
          event.requestId,
          undefined,
          (message) => ({ ...message, messageId: event.messageId, status: 'queued' }),
        ));
        break;
      case 'SEND_REJECTED':
        clearRequestTimer(event.requestId);
        setMessages((current) => updateMessage(
          current,
          event.requestId,
          undefined,
          (message) => ({ ...message, status: 'failed', error: event.reason }),
        ));
        break;
      case 'DELIVERY_ACK':
        setPackets((current) => current.filter(
          (packet) => packet.id !== event.messageId || packet.target !== event.nodeId,
        ));
        setMessages((current) => updateMessage(
          current,
          undefined,
          event.messageId,
          (message) => message.targetId !== event.nodeId
            ? {
                ...message,
                status: 'unconfirmed',
                error: `ACK came from ${event.nodeId}, not destination ${message.targetId}.`,
              }
            : {
                ...message,
                status: 'delivered',
                response: event.response ?? `Acknowledged by ${event.nodeId}`,
              },
        ));
        break;
      case 'MESSAGE_ECHO':
        setPackets((current) => current.filter(
          (packet) => packet.id !== event.messageId || packet.target !== event.nodeId,
        ));
        setMessages((current) => updateMessage(
          current,
          undefined,
          event.messageId,
          (message) => message.targetId !== event.nodeId
            ? {
                ...message,
                status: 'unconfirmed',
                error: `Echo came from ${event.nodeId}, not destination ${message.targetId}.`,
              }
            : {
                ...message,
                status: 'echo_received',
                response: event.payload,
              },
        ));
        break;
    }
  }, [clearRequestTimer]);

  useEffect(() => {
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let activeSocket: WebSocket | null = null;
    const requestTimers = requestTimersRef.current;

    const connect = () => {
      if (disposed) return;
      setConnectionStatus((status) => status === 'connecting' ? 'connecting' : 'reconnecting');
      try {
        const socket = new WebSocket(BRIDGE_URL);
        activeSocket = socket;
        socketRef.current = socket;

        socket.onopen = () => {
          if (disposed) return;
          setConnectionStatus('connected');
          setError(null);
        };
        socket.onmessage = (message) => {
          if (disposed) return;
          try {
            handleEvent(JSON.parse(message.data) as unknown);
          } catch (eventError) {
            const detail = eventError instanceof Error ? eventError.message : 'Invalid bridge event';
            setError(`Bridge event rejected: ${detail}`);
          }
        };
        socket.onerror = () => {
          if (!disposed) setError('WebSocket connection error.');
        };
        socket.onclose = () => {
          if (activeSocket === socket) activeSocket = null;
          if (socketRef.current === socket) socketRef.current = null;
          if (disposed) return;
          setConnectionStatus('reconnecting');
          retryTimer = setTimeout(connect, 2000);
        };
      } catch (connectError) {
        if (disposed) return;
        const detail = connectError instanceof Error ? connectError.message : 'Unable to open WebSocket';
        setError(detail);
        setConnectionStatus('reconnecting');
        retryTimer = setTimeout(connect, 2000);
      }
    };

    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      for (const timer of requestTimers.values()) clearTimeout(timer);
      requestTimers.clear();
      const socket = activeSocket;
      activeSocket = null;
      socket?.close();
    };
  }, [handleEvent]);

  const sendMessage = useCallback((targetId: string, payload: string) => {
    const requestId = crypto.randomUUID();
    const createdAt = Date.now();
    const byteLength = new TextEncoder().encode(payload).length;
    let status: OutboundMessage['status'] = 'sending';
    let sendError: string | undefined;

    if (!targetId.trim()) {
      status = 'failed';
      sendError = 'Select a destination peer.';
    } else if (!payload.trim()) {
      status = 'failed';
      sendError = 'Enter a message before sending.';
    } else if (byteLength > maxPayloadBytes) {
      status = 'failed';
      sendError = `Message is ${byteLength} bytes; the current packet payload limit is ${maxPayloadBytes} bytes.`;
    } else if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      status = 'failed';
      sendError = 'The bridge is not connected.';
    }

    const message: OutboundMessage = {
      requestId,
      targetId,
      payload,
      status,
      createdAt,
      error: sendError,
    };
    setMessages((current) => [...current, message].slice(-MAX_HISTORY));

    if (status !== 'sending') {
      setError(sendError ?? 'Message could not be sent.');
      return requestId;
    }

    const request: SendMessageRequest = { type: 'SEND_MESSAGE', requestId, targetId, payload };
    try {
      socketRef.current?.send(JSON.stringify(request));
      setError(null);
      requestTimersRef.current.set(requestId, setTimeout(() => {
        requestTimersRef.current.delete(requestId);
        setMessages((current) => updateMessage(
          current,
          requestId,
          undefined,
          (currentMessage) => currentMessage.status === 'sending'
            ? {
                ...currentMessage,
                status: 'unconfirmed',
                error: 'No bridge acceptance reply. The BLE node may still be processing the send request.',
              }
            : currentMessage,
        ));
      }, REQUEST_TIMEOUT_MS));
    } catch (sendErrorValue) {
      clearRequestTimer(requestId);
      const detail = sendErrorValue instanceof Error ? sendErrorValue.message : 'WebSocket send failed';
      setMessages((current) => updateMessage(
        current,
        requestId,
        undefined,
        (currentMessage) => ({ ...currentMessage, status: 'failed', error: detail }),
      ));
      setError(detail);
    }
    return requestId;
  }, [clearRequestTimer, maxPayloadBytes]);

  return {
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
    sendMessage,
  };
}
