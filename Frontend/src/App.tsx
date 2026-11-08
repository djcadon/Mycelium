import { useState } from 'react';
import { NetworkHUD } from './components/NetworkHUD';
import { useMeshBridge } from './hooks/useMeshBridge';

export function App() {
  const {
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
  } = useMeshBridge();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const resolvedSelectedNodeId = selectedNodeId && nodes.some((node) => node.id === selectedNodeId)
    ? selectedNodeId
    : null;
  const selectedNode = nodes.find((node) => node.id === resolvedSelectedNodeId) || null;

  return (
    <div className="min-h-screen bg-canvas px-4 md:px-10 pb-6 text-ink">
      <NetworkHUD
        nodes={nodes}
        links={links}
        routes={routes}
        packets={packets}
        alerts={alerts}
        messages={messages}
        maxPayloadBytes={maxPayloadBytes}
        connectionStatus={connectionStatus}
        lastEventAt={lastEventAt}
        error={error}
        onSendMessage={sendMessage}
        selectedNode={selectedNode}
        selectedNodeId={resolvedSelectedNodeId}
        onSelectNode={setSelectedNodeId}
      />
    </div>
  );
}

export default App;
