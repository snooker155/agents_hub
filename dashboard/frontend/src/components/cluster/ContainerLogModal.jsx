import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { X } from 'lucide-react';
import { getContainerLogs, getInstanceLogs } from '../../api';
import { useChannel } from '../stream';
import { useI18n } from '../../i18n';
import PageLoader from '../PageLoader';

/**
 * A container's log on the Cluster page.
 *
 * Two sources, as tabs when the container carries an instance:
 * - ``output``: what the container prints (``docker logs`` through
 *   GET /api/containers/{name}/logs, tailed live on ``logs:container:<name>``).
 *   Only the daemon this backend talks to can give it, so a container on
 *   another worker's daemon (``remote``) has no output tab.
 * - ``instance``: the instance's carrier log (GET /api/instances/{id}/logs,
 *   live on ``logs:instance:<id>``): how its start went, step by step, and
 *   everything the process wrote, from any host through the object store.
 */
export default function ContainerLogModal({ container, onClose }) {
  const { t } = useI18n();
  const instanceId = container.owner_kind === 'instance' ? container.owner_id : null;
  const canOutput = !container.remote;
  const [tab, setTab] = useState(canOutput ? 'output' : 'instance');
  const [logs, setLogs] = useState('');
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef(null);
  const live = String(container.state || container.status || '').toLowerCase().includes('run');

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- a new source starts from its loader
    setLoading(true);
    const load = tab === 'output'
      ? getContainerLogs(container.name, 500).then(({ data }) => (typeof data === 'string' ? data : ''))
      : getInstanceLogs(instanceId).then(({ data }) => data?.logs || '');
    load
      .then((text) => { if (!cancelled) setLogs(text || t('cluster.noLogs')); })
      .catch((e) => { if (!cancelled) setLogs(e?.response?.data?.detail || e.message || t('cluster.noLogs')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [tab, container.name, instanceId, t]);

  const channel = !live ? null
    : tab === 'output' ? `logs:container:${container.name}`
      : instanceId ? `logs:instance:${instanceId}` : null;
  useChannel(channel, (ev) => {
    if (ev.type === 'logs') setLogs(ev.content || t('cluster.noLogs'));
  });

  useEffect(() => { bottomRef.current?.scrollIntoView?.(); }, [logs]);

  const tabs = [
    canOutput && { key: 'output', label: t('cluster.containerLog.output') },
    instanceId && { key: 'instance', label: t('cluster.containerLog.instance') },
  ].filter(Boolean);

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-gray-800">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-gray-200 text-sm font-semibold truncate font-mono">{container.name}</span>
            {tabs.length > 1 && (
              <div className="flex rounded-lg border border-gray-700 overflow-hidden text-xs">
                {tabs.map((x) => (
                  <button key={x.key} onClick={() => setTab(x.key)}
                          className={`px-2.5 py-1 ${tab === x.key ? 'bg-gray-700 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
                    {x.label}
                  </button>
                ))}
              </div>
            )}
            {instanceId && (
              <Link to={`/instances/${instanceId}`} className="text-xs text-indigo-300 hover:underline shrink-0">
                {t('cluster.containerLog.openInstance')}
              </Link>
            )}
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors" title={t('cluster.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        {!canOutput && (
          <p className="px-5 pt-3 text-xs text-amber-300">
            {t('cluster.containerLog.remote', { host: container.host || container.launched_on || '?' })}
          </p>
        )}
        <div className="flex-1 overflow-auto p-5">
          {!canOutput && !instanceId ? (
            <p className="text-xs text-gray-400">{t('cluster.containerLog.unavailable')}</p>
          ) : loading ? (
            <PageLoader size="sm" />
          ) : (
            <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{logs}</pre>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}
