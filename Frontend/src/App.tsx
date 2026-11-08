import { useState } from 'react';
import { NetworkHUD } from './components/NetworkHUD';
import { useMeshBridge } from './hooks/useMeshBridge';

export function App() {
  const {
    nodes,
    links,
    packets,
    alerts,
    messages,
    connectionStatus,
    lastEventAt,
    error,
    sendMessage,
  } = useMeshBridge();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const resolvedSelectedNodeId = selectedNodeId && nodes.some((node) => node.id === selectedNodeId)
    ? selectedNodeId
    : nodes.find((node) => node.hardware !== 'Host Controller')?.id ?? null;
  const selectedNode = nodes.find((node) => node.id === resolvedSelectedNodeId) || null;

  return (
    <div className="app-shell">
      <NetworkHUD
        nodes={nodes}
        links={links}
        packets={packets}
        alerts={alerts}
        messages={messages}
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
