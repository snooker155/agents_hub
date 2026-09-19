import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { X, Search, Eye, Check, Loader2 } from 'lucide-react';
import { getContextKinds, getContextEntities, getContextEntityPreview } from '../api';
import { useI18n } from '../i18n';

// Picker for the hub records a chat message can carry alongside its files — a
// task, a view, a project, a scenario, a loop, a flow, a team, an agent, a
// scheduled job. The kind rail and every row come from /api/context, which is a
// shell over the same catalog the prompt builder renders from: whatever is
// offered here is guaranteed to arrive in the agent's context.
//
// The eye button answers the question the chip cannot: *exactly* what text does
// attaching this send? It fetches the rendered block the agent will receive.
export default function ContextEntityPicker({
  initialKind,
  workspace,
  projectId,
  selected = [],
  onAdd,
  onClose,
}) {
  const { t } = useI18n();
  const [kinds, setKinds] = useState([]);
  const [kind, setKind] = useState(initialKind || '');
  const [query, setQuery] = useState('');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Local picks, keyed "kind:id". Committed to the composer only on "Add", so
  // browsing between kinds never mutates the pending message.
  const [picked, setPicked] = useState({});
  const [preview, setPreview] = useState(null); // {kind, id, title, body} | {loading}
  const searchRef = useRef(null);

  // Already-attached references are shown as picked-and-locked rather than
  // hidden, so the list does not silently reshuffle between openings.
  const alreadyAttached = useMemo(
    () => new Set((selected || []).map((r) => `${r.kind}:${r.id}`)),
    [selected],
  );

  useEffect(() => {
    getContextKinds()
      .then((r) => {
        const list = r.data || [];
        setKinds(list);
        setKind((k) => k || list[0]?.kind || '');
      })
      .catch(() => setError(t('chat.picker.loadKindsFailed')));
  }, [t]);

  useEffect(() => { searchRef.current?.focus(); }, []);

  // Debounced so typing in a workspace with thousands of tasks does not fire a
  // request per keystroke.
  useEffect(() => {
    if (!kind) return undefined;
    let cancelled = false;
    const handle = setTimeout(() => {
      setLoading(true);
      setError('');
      getContextEntities(kind, {
        workspace: workspace || undefined,
        project_id: projectId || undefined,
        q: query || undefined,
      })
        .then((r) => { if (!cancelled) setItems(r.data || []); })
        .catch(() => { if (!cancelled) { setItems([]); setError(t('chat.picker.loadFailed')); } })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, query ? 250 : 0);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [kind, query, workspace, projectId, t]);

  const togglePick = useCallback((item) => {
    const refKey = `${item.kind}:${item.id}`;
    if (alreadyAttached.has(refKey)) return;
    setPicked((prev) => {
      const next = { ...prev };
      if (next[refKey]) delete next[refKey];
      else next[refKey] = { kind: item.kind, id: item.id, label: item.label, icon: item.icon, url: item.url };
      return next;
    });
  }, [alreadyAttached]);

  const showPreview = useCallback((item) => {
    setPreview({ kind: item.kind, id: item.id, title: item.label, loading: true });
    getContextEntityPreview(item.kind, item.id)
      .then((r) => setPreview({ ...r.data, loading: false }))
      .catch(() => setPreview({ kind: item.kind, id: item.id, title: item.label, body: '', error: true }));
  }, []);

  const pickedList = Object.values(picked);

  const commit = () => {
    if (pickedList.length) onAdd?.(pickedList);
    onClose();
  };

  const kindLabel = (k) => t(`chat.entityKind.${k.kind}`, { defaultValue: k.noun });

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl w-full max-w-4xl h-[32rem] shadow-xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <div>
            <h3 className="text-sm font-semibold text-gray-800">{t('chat.picker.title')}</h3>
            <p className="text-xs text-gray-500">{t('chat.picker.subtitle')}</p>
          </div>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 flex min-h-0">
          {/* Kind rail */}
          <div className="w-44 flex-shrink-0 border-r border-gray-200 overflow-y-auto py-2">
            {kinds.map((k) => (
              <button
                key={k.kind}
                type="button"
                onClick={() => { setKind(k.kind); setPreview(null); }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-left text-sm transition-colors ${
                  k.kind === kind ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                <span aria-hidden="true">{k.icon}</span>
                <span className="truncate">{kindLabel(k)}</span>
              </button>
            ))}
          </div>

          {/* Candidates */}
          <div className="flex-1 flex flex-col min-w-0">
            <div className="px-3 py-2 border-b border-gray-100">
              <div className="flex items-center gap-2 w-full bg-gray-100 border border-gray-200 rounded-lg px-2 py-1.5 focus-within:border-indigo-400 transition-colors">
                <Search className="w-4 h-4 text-gray-400 flex-shrink-0" />
                <input
                  ref={searchRef}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={t('chat.picker.searchPlaceholder')}
                  className="flex-1 text-sm focus:outline-none bg-transparent"
                />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              {loading && (
                <div className="flex items-center gap-2 px-4 py-6 text-sm text-gray-400">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('chat.picker.loading')}
                </div>
              )}
              {!loading && error && <p className="px-4 py-6 text-sm text-red-600">{error}</p>}
              {!loading && !error && items.length === 0 && (
                <p className="px-4 py-6 text-sm text-gray-400">{t('chat.picker.empty')}</p>
              )}
              {!loading && items.map((item) => {
                const refKey = `${item.kind}:${item.id}`;
                const attached = alreadyAttached.has(refKey);
                const isPicked = Boolean(picked[refKey]);
                return (
                  <div
                    key={refKey}
                    className={`group flex items-center gap-2 px-3 py-2 border-b border-gray-50 ${
                      attached ? 'opacity-50' : 'cursor-pointer hover:bg-gray-50'
                    } ${isPicked ? 'bg-indigo-50/60' : ''}`}
                    onClick={() => togglePick(item)}
                  >
                    <span
                      className={`w-4 h-4 flex-shrink-0 rounded border flex items-center justify-center ${
                        isPicked || attached ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-gray-300'
                      }`}
                    >
                      {(isPicked || attached) && <Check className="w-3 h-3" />}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-gray-800 truncate">{item.label}</div>
                      {item.subtitle && (
                        <div className="text-[11px] text-gray-400 truncate">{item.subtitle}</div>
                      )}
                    </div>
                    {attached && (
                      <span className="text-[10px] uppercase tracking-wide text-indigo-500 flex-shrink-0">
                        {t('chat.picker.alreadyAttached')}
                      </span>
                    )}
                    <button
                      type="button"
                      title={t('chat.picker.preview')}
                      onClick={(e) => { e.stopPropagation(); showPreview(item); }}
                      className="flex-shrink-0 p-1 text-gray-300 hover:text-indigo-600 group-hover:text-gray-500"
                    >
                      <Eye className="w-4 h-4" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          {/* What the agent will actually receive for the previewed row. */}
          {preview && (
            <div className="w-80 flex-shrink-0 border-l border-gray-200 flex flex-col min-h-0">
              <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
                <span className="text-xs font-semibold text-gray-600 truncate">{preview.title}</span>
                <button type="button" onClick={() => setPreview(null)} className="text-gray-400 hover:text-gray-700">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="flex-1 overflow-auto px-3 py-2">
                {preview.loading && <p className="text-xs text-gray-400">{t('chat.picker.loading')}</p>}
                {preview.error && <p className="text-xs text-red-600">{t('chat.picker.previewFailed')}</p>}
                {!preview.loading && !preview.error && (
                  <pre className="text-[11px] text-gray-600 whitespace-pre-wrap break-words">{preview.body}</pre>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200">
          <span className="text-xs text-gray-500">
            {t('chat.picker.pickedCount', { count: pickedList.length })}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800"
            >
              {t('chat.picker.cancel')}
            </button>
            <button
              type="button"
              onClick={commit}
              disabled={!pickedList.length}
              className="px-3 py-1.5 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700
                disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {t('chat.picker.add')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
