/**
 * Widgets page: the embeddable chat widgets of the current workspace
 * (widgets/, dashboard/backend/routes/widget.py, docs/widget.md).
 *
 * The list on the left; the selected widget on the right, in four tabs:
 * Embed (the script tag, the key and its rotation, on/off), Preview (the
 * real script in a frame), Conversations (every visitor's threads, with a
 * link to the run behind each reply) and Settings (the same form the create
 * modal uses).
 */
import { useCallback, useEffect, useState } from 'react';
import { Loader, MessageSquareCode, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useWorkspace } from '../components/workspace';
import { errorDetail, useToast } from '../components/toast';
import { useI18n } from '../i18n';
import { deleteWidget, getWidgetOptions, getWidgets } from '../api/widgets';
import WidgetForm from '../components/widgets/WidgetForm';
import WidgetEmbed from '../components/widgets/WidgetEmbed';
import WidgetPreview from '../components/widgets/WidgetPreview';
import WidgetConversations from '../components/widgets/WidgetConversations';
import { ACCENT_SWATCHES, FALLBACK_OPTIONS } from '../components/widgets/widgetUtils';
import PageLoader from '../components/PageLoader';

const TABS = ['embed', 'preview', 'conversations', 'settings'];

function CreateModal({ workspace, options, onClose, onSaved }) {
  const { t } = useI18n();
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="widget-create-title"
        className="bg-white rounded-xl shadow-xl w-full max-w-xl p-6 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 id="widget-create-title" className="text-lg font-bold text-gray-900 flex items-center gap-2">
            <MessageSquareCode className="w-5 h-5 text-indigo-600" /> {t('widgets.newWidget')}
          </h2>
          <button type="button" onClick={onClose} aria-label={t('widgets.form.cancel')}
            className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X className="w-5 h-5" />
          </button>
        </div>
        <WidgetForm workspace={workspace} options={options} onSaved={onSaved} onCancel={onClose} />
      </div>
    </div>
  );
}

function WidgetRow({ widget, selected, onSelect, t }) {
  return (
    <li>
      <button type="button" onClick={() => onSelect(widget.widget_id)} aria-current={selected ? 'true' : undefined}
        className={`w-full text-left rounded-lg border px-3 py-2.5 transition-colors ${
          selected ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 bg-white hover:bg-gray-50'}`}>
        <span className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${ACCENT_SWATCHES[widget.accent] || 'bg-gray-400'}`} aria-hidden="true" />
          <span className="font-medium text-sm text-gray-900 truncate flex-1">{widget.name}</span>
          <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${
            widget.enabled && widget.owner_ok ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}>
            {widget.enabled ? t('widgets.on') : t('widgets.off')}
          </span>
        </span>
        <span className="block text-xs text-gray-500 mt-1 truncate">
          {t('widgets.agent')}: {widget.agent_name || widget.agent_id}
        </span>
        <span className="block text-xs text-gray-400 mt-0.5">
          {t('widgets.threadsCount', { count: widget.thread_count || 0 })}
          {' · '}
          {t('widgets.tokensToday', { count: widget.tokens_today || 0 })}
        </span>
      </button>
    </li>
  );
}

export default function Widgets() {
  const { t } = useI18n();
  const toast = useToast();
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || 'default';
  const [widgets, setWidgets] = useState([]);
  const [options, setOptions] = useState(FALLBACK_OPTIONS);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(null);
  const [tab, setTab] = useState('embed');
  const [creating, setCreating] = useState(false);

  const fetchWidgets = useCallback(async () => {
    try {
      const { data } = await getWidgets(workspace);
      const rows = Array.isArray(data) ? data : [];
      setWidgets(rows);
      setSelectedId((current) => (rows.some((w) => w.widget_id === current) ? current : rows[0]?.widget_id || null));
    } catch (err) {
      toast.error(t('widgets.loadFailed'), errorDetail(err));
    } finally {
      setLoading(false);
    }
  }, [workspace, toast, t]);

  useEffect(() => { setLoading(true); fetchWidgets(); }, [fetchWidgets]);

  useEffect(() => {
    getWidgetOptions().then(({ data }) => { if (data) setOptions(data); }).catch(() => {});
  }, []);

  const selected = widgets.find((w) => w.widget_id === selectedId) || null;

  const replace = (updated) => {
    setWidgets((rows) => rows.map((w) => (w.widget_id === updated.widget_id ? { ...w, ...updated } : w)));
  };

  const remove = async () => {
    if (!selected || !window.confirm(t('widgets.confirmDelete', { name: selected.name }))) return;
    try {
      await deleteWidget(selected.widget_id);
      toast.success(t('widgets.deleted'));
      setSelectedId(null);
      fetchWidgets();
    } catch (err) {
      toast.error(t('widgets.actionFailed'), errorDetail(err));
    }
  };

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={MessageSquareCode}
        title={t('widgets.title')}
        description={t('widgets.pageDescription')}
        actions={<>
          <button type="button" onClick={fetchWidgets}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            <RefreshCw className="w-4 h-4" /> {t('widgets.refresh')}
          </button>
          <button type="button" onClick={() => setCreating(true)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700">
            <Plus className="w-4 h-4" /> {t('widgets.newWidget')}
          </button>
        </>}
      />

      {loading ? (
        <div className="bg-white rounded-xl border border-gray-200"><PageLoader /></div>
      ) : widgets.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 text-center py-16 px-6">
          <MessageSquareCode className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-700 text-sm font-medium">{t('widgets.empty')}</p>
          <p className="text-gray-500 text-sm mt-1">{t('widgets.emptyHint')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] gap-6 items-start">
          <ul className="space-y-2" aria-label={t('widgets.title')}>
            {widgets.map((w) => (
              <WidgetRow key={w.widget_id} widget={w} selected={w.widget_id === selectedId}
                onSelect={setSelectedId} t={t} />
            ))}
          </ul>

          {selected && (
            <section className="bg-white rounded-xl border border-gray-200" aria-label={selected.name}>
              <div className="flex items-center justify-between gap-3 px-4 pt-4">
                <div className="min-w-0">
                  <h2 className="text-base font-bold text-gray-900 truncate">{selected.name}</h2>
                  <p className="text-xs text-gray-500 font-mono">{selected.widget_id}</p>
                </div>
                <button type="button" onClick={remove} title={t('widgets.delete')} aria-label={t('widgets.delete')}
                  className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              <div role="tablist" className="flex gap-1 px-4 mt-3 border-b border-gray-100">
                {TABS.map((key) => (
                  <button key={key} type="button" role="tab" aria-selected={tab === key}
                    id={`widget-tab-${key}`} aria-controls={`widget-panel-${key}`}
                    onClick={() => setTab(key)}
                    className={`px-3 py-2 text-sm -mb-px border-b-2 ${
                      tab === key ? 'border-indigo-600 text-indigo-700 font-medium' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
                    {t(`widgets.tabs.${key}`)}
                  </button>
                ))}
              </div>
              <div role="tabpanel" id={`widget-panel-${tab}`} aria-labelledby={`widget-tab-${tab}`} className="p-4">
                {tab === 'embed' && <WidgetEmbed widget={selected} onChanged={replace} />}
                {tab === 'preview' && <WidgetPreview widget={selected} />}
                {tab === 'conversations' && <WidgetConversations key={selected.widget_id} widget={selected} />}
                {tab === 'settings' && (
                  <WidgetForm key={`${selected.widget_id}:${selected.updated_at}`} widget={selected}
                    workspace={workspace} options={options}
                    onSaved={(data) => { replace(data); toast.success(t('widgets.form.saved')); }} />
                )}
              </div>
            </section>
          )}
        </div>
      )}

      {creating && (
        <CreateModal workspace={workspace} options={options} onClose={() => setCreating(false)}
          onSaved={(data) => {
            setCreating(false);
            toast.success(t('widgets.form.created'));
            setSelectedId(data.widget_id);
            setTab('embed');
            fetchWidgets();
          }} />
      )}
    </PageContainer>
  );
}
