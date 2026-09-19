import React, { useState, useEffect } from 'react';
import { getFactoryLogs } from '../../api';
import { useStream } from '../stream';
import { useI18n } from '../../i18n';

function LogPanel({ workspace }) {
  const { t } = useI18n();
  const [logs, setLogs] = useState([]);
  const { on } = useStream();

  useEffect(() => {
    if (!workspace) return;

    const fetchLogs = async () => {
      try {
        const res = await getFactoryLogs(workspace);
        setLogs(res.data);
      } catch {
        // console.error("Failed to fetch logs", e);
      }
    };

    fetchLogs();
    return on('app', (ev) => { if (ev.type === 'flow_runs.changed') fetchLogs(); });
  }, [workspace, on]);

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-medium text-gray-900 border-b pb-2">{t('flowLogPanel.interactionLogs')}</h3>
      <div className="space-y-3">
        {logs.length === 0 ? (
          <p className="text-sm text-gray-500 italic">{t('flowLogPanel.noLogsYetForThis')}</p>
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
