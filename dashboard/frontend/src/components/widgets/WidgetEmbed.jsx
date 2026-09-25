/**
 * The Embed tab: the script tag to paste, the publishable key and its
 * rotation, the on/off switch, and the origins the widget works on.
 */
import { useEffect, useState } from 'react';
import { Check, Copy, KeyRound, Loader, Power, AlertTriangle } from 'lucide-react';
import { getWidgetSnippet, rotateWidgetKey, updateWidget } from '../../api/widgets';
import { useI18n } from '../../i18n';
import { errorDetail, useToast } from '../toast';

export default function WidgetEmbed({ widget, onChanged }) {
  const { t } = useI18n();
  const toast = useToast();
  const [snippet, setSnippet] = useState('');
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState('');

  useEffect(() => {
    let alive = true;
    getWidgetSnippet(widget.widget_id)
      .then(({ data }) => { if (alive) setSnippet(data?.snippet || ''); })
      .catch(() => { if (alive) setSnippet(''); });
    return () => { alive = false; };
  }, [widget.widget_id, widget.public_key]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error(t('widgets.embed.copyFailed'));
    }
  };

  const rotate = async () => {
    if (!window.confirm(t('widgets.embed.confirmRotate', { name: widget.name }))) return;
    setBusy('rotate');
    try {
      const { data } = await rotateWidgetKey(widget.widget_id);
      toast.success(t('widgets.embed.rotated'));
      onChanged(data);
    } catch (err) {
      toast.error(t('widgets.actionFailed'), errorDetail(err));
    } finally {
      setBusy('');
    }
  };

  const toggle = async () => {
    setBusy('toggle');
    try {
      const { data } = await updateWidget(widget.widget_id, { enabled: !widget.enabled });
      toast.success(t('widgets.embed.switched'));
      onChanged(data);
    } catch (err) {
      toast.error(t('widgets.actionFailed'), errorDetail(err));
    } finally {
      setBusy('');
    }
  };

  const origins = widget.allowed_origins || [];

  return (
    <div className="space-y-5">
      {!widget.owner_ok && (
        <div role="alert" className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" /> {t('widgets.ownerLost')}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 p-3">
        <p className="text-sm text-gray-700">
          {widget.enabled ? t('widgets.embed.statusOn') : t('widgets.embed.statusOff')}
        </p>
        <button type="button" onClick={toggle} disabled={busy === 'toggle'}
          className="flex items-center gap-2 px-3 py-1.5 text-sm bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-60">
          {busy === 'toggle' ? <Loader className="w-4 h-4 animate-spin" /> : <Power className="w-4 h-4" />}
          {widget.enabled ? t('widgets.embed.switchOff') : t('widgets.embed.switchOn')}
        </button>
      </div>

      <section>
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-sm font-bold text-gray-900">{t('widgets.embed.snippet')}</h3>
          <button type="button" onClick={copy} disabled={!snippet}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
            {copied ? t('widgets.embed.copied') : t('widgets.embed.copy')}
          </button>
        </div>
        <pre className="text-xs bg-gray-50 border border-gray-200 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all font-mono text-gray-800"
          data-testid="widget-snippet">{snippet}</pre>
        <p className="text-xs text-gray-400 mt-1">{t('widgets.embed.snippetHint')}</p>
      </section>

      <section>
        <h3 className="text-sm font-bold text-gray-900 mb-1">{t('widgets.embed.key')}</h3>
        <div className="flex flex-wrap items-center gap-2">
          <code className="text-xs bg-gray-50 border border-gray-200 rounded px-2 py-1 font-mono text-gray-700 break-all">
            {widget.public_key}
          </code>
          <button type="button" onClick={rotate} disabled={busy === 'rotate'}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs text-amber-700 bg-white border border-amber-200 rounded-lg hover:bg-amber-50 disabled:opacity-60">
            {busy === 'rotate' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <KeyRound className="w-3.5 h-3.5" />}
            {t('widgets.embed.rotate')}
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-1">{t('widgets.embed.keyHint')}</p>
      </section>

      <section>
        <h3 className="text-sm font-bold text-gray-900 mb-1">{t('widgets.embed.origins')}</h3>
        {origins.length ? (
          <ul className="flex flex-wrap gap-1.5">
            {origins.map((o) => (
              <li key={o} className="text-xs font-mono bg-gray-50 border border-gray-200 rounded px-2 py-0.5 text-gray-700">{o}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-amber-700">{t('widgets.embed.noOrigins')}</p>
        )}
      </section>

      <section>
        <h3 className="text-sm font-bold text-gray-900 mb-1">{t('widgets.embed.openFromPage')}</h3>
        <p className="text-xs text-gray-500">{t('widgets.embed.openFromPageHint')}</p>
      </section>
    </div>
  );
}
