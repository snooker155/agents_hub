import React, { useState, useEffect } from 'react';
import { getFactoryLogs } from '../../api';

function LogPanel({ workspace }) {
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    if (!workspace) return;

    const fetchLogs = async () => {
      try {
        const res = await getFactoryLogs(workspace);
        setLogs(res.data);
      } catch (e) {
        // console.error("Failed to fetch logs", e);
      }
    };

    fetchLogs();
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, [workspace]);

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-medium text-gray-900 border-b pb-2">Interaction Logs</h3>
      <div className="space-y-3">
        {logs.length === 0 ? (
          <p className="text-sm text-gray-500 italic">No logs yet for this workspace.</p>
        ) : (
          logs.map((log, i) => (
            <div key={i} className="text-xs bg-gray-50 p-2 rounded border border-gray-100">
              <span className="text-gray-400">[{new Date(log.timestamp).toLocaleTimeString()}]</span> <span className="text-indigo-600 font-bold">{log.type}:</span> {log.content}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default LogPanel;
