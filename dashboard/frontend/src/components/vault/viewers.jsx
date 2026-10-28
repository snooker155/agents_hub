import { useEffect, useMemo, useState } from 'react';
import { Download } from 'lucide-react';

import { useI18n } from '../../i18n';
import { b64decode } from '../../lib/syncCrypto';
import { formatBytes, formatWhen } from './format';

/**
 * Read-only viewers for decrypted (or plain) synced records. Each takes the
 * decoded `doc` ({ kind, id, title, data }); nothing here writes or stores.
 */

const Section = ({ title, children }) => (
  <section className="mb-4">
    {title && <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">{title}</h3>}
    {children}
  </section>
);

const Text = ({ children }) => (
  <pre className="whitespace-pre-wrap break-words text-sm text-gray-800 bg-gray-50 border border-gray-200 rounded-lg p-3 font-sans">
    {children}
  </pre>
);

const Row = ({ label, value }) => (
  value === undefined || value === null || value === '' ? null : (
    <div className="flex flex-col sm:flex-row sm:gap-3 text-sm py-0.5">
      <span className="text-gray-500 sm:w-32 shrink-0">{label}</span>
      <span className="text-gray-800 break-words min-w-0">{String(value)}</span>
    </div>
  )
);

const asText = (v) => (typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v, null, 2));

export function JsonTree({ value, label, depth = 0 }) {
  const { t } = useI18n();
  const isObj = value !== null && typeof value === 'object';
  if (!isObj) {
    return (
      <div className="text-sm break-words" style={{ paddingLeft: depth ? 12 : 0 }}>
        {label !== undefined && <span className="text-gray-500">{label}: </span>}
        <span className="text-gray-800 whitespace-pre-wrap">{typeof value === 'string' ? value : JSON.stringify(value)}</span>
      </div>
    );
  }
  const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
  if (!entries.length) {
    return (
      <div className="text-sm text-gray-400" style={{ paddingLeft: depth ? 12 : 0 }}>
        {label !== undefined && <span className="text-gray-500">{label}: </span>}{t('vault.viewer.empty')}
      </div>
    );
  }
  return (
    <details open={depth < 1} style={{ paddingLeft: depth ? 12 : 0 }} className="text-sm">
      <summary className="cursor-pointer text-gray-600">{label !== undefined ? String(label) : t('vault.viewer.data')}</summary>
      {entries.map(([k, v]) => <JsonTree key={String(k)} value={v} label={k} depth={depth + 1} />)}
    </details>
  );
}

export function ChatViewer({ doc }) {
  const { t } = useI18n();
  const messages = doc.data?.messages || [];
  if (!messages.length) return <p className="text-sm text-gray-500">{t('vault.viewer.noMessages')}</p>;
  return (
    <div className="space-y-2">
      {messages.map((m, i) => {
        const mine = m.role === 'user';
        const label = ['user', 'assistant', 'system', 'tool'].includes(m.role) ? t(`vault.viewer.${m.role}`) : m.role;
        return (
          <div key={i} className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[90%] rounded-lg px-3 py-2 border ${mine ? 'bg-indigo-50 border-indigo-100' : 'bg-white border-gray-200'}`}>
              <div className="text-[11px] font-medium text-gray-500 mb-0.5">{label}</div>
              <div className="text-sm text-gray-800 whitespace-pre-wrap break-words">{asText(m.content)}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function MemoryViewer({ doc }) {
  const { t } = useI18n();
  const { blocks = [], notes = [] } = doc.data || {};
  return (
    <>
      <Section title={t('vault.viewer.blocks')}>
        {blocks.length ? blocks.map((b, i) => (
          <div key={b.id || i} className="mb-2">
            {(b.title || b.name) && <div className="text-sm font-medium text-gray-700">{b.title || b.name}</div>}
            <Text>{asText(b.content ?? b.text ?? b)}</Text>
          </div>
        )) : <p className="text-sm text-gray-500">{t('vault.viewer.noBlocks')}</p>}
      </Section>
      <Section title={t('vault.viewer.notes')}>
        {notes.length ? notes.map((n, i) => (
          <div key={n.id || i} className="mb-2"><Text>{asText(n.content ?? n.text ?? n.note ?? n)}</Text></div>
        )) : <p className="text-sm text-gray-500">{t('vault.viewer.noNotes')}</p>}
      </Section>
    </>
  );
}

const SPEC_SKIP = new Set(['prompts', 'id', 'remote']);

export function AgentViewer({ doc }) {
  const { t } = useI18n();
  const data = doc.data || {};
  const prompts = data.prompts || {};
  const spec = Object.fromEntries(Object.entries(data).filter(([k, v]) => !SPEC_SKIP.has(k) && v !== null && v !== ''));
  return (
    <>
      {['instructions', 'capabilities', 'usage', 'examples'].map((key) => (
        prompts[key] ? <Section key={key} title={t(`vault.viewer.${key}`)}><Text>{asText(prompts[key])}</Text></Section> : null
      ))}
      <Section title={t('vault.viewer.spec')}><JsonTree value={spec} /></Section>
    </>
  );
}

export function SkillViewer({ doc }) {
  const { t } = useI18n();
  const d = doc.data || {};
  return (
    <>
      <Section>
        <Row label={t('vault.viewer.name')} value={d.name || doc.title} />
        <Row label={t('vault.viewer.description')} value={d.description} />
      </Section>
      <Section title={t('vault.viewer.body')}><Text>{asText(d.body)}</Text></Section>
    </>
  );
}

const isTextMime = (mime, name = '') => /^text\//.test(mime || '') || /json|xml|yaml|javascript/.test(mime || '')
  || /\.(md|txt|csv|py|js|json|ya?ml|toml|log)$/i.test(name);

export function FileViewer({ doc }) {
  const { t } = useI18n();
  const d = doc.data || {};
  const bytes = useMemo(() => {
    if (!d.content_b64) return null;
    try { return b64decode(d.content_b64); } catch { return null; }
  }, [d.content_b64]);
  const mime = d.mime_type || 'application/octet-stream';
  // A blob URL pins the decrypted bytes in the document until it is revoked,
  // and this tab may stay open for days. Revoking on unmount (Lock, Back) and
  // on new bytes keeps the plaintext from outliving the viewer.
  const [objectUrl, setObjectUrl] = useState(null);
  useEffect(() => {
    if (!bytes || typeof URL.createObjectURL !== 'function') {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the URL is owned by this effect, so it is set here too
      setObjectUrl(null);
      return undefined;
    }
    const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
    setObjectUrl(url);
    return () => { URL.revokeObjectURL(url); };
  }, [bytes, mime]);

  const download = () => {
    if (!objectUrl) return;
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = d.name || 'file';
    a.click();
  };

  let preview = null;
  if (bytes && /^image\//.test(mime) && mime !== 'image/svg+xml') {
    preview = <img src={objectUrl} alt={d.name} className="max-w-full max-h-96 rounded-lg border border-gray-200" />;
  } else if (bytes && isTextMime(mime, d.name)) {
    preview = <Text>{new TextDecoder().decode(bytes.slice(0, 200000))}</Text>;
  }

  return (
    <>
      <Section>
        <Row label={t('vault.viewer.name')} value={d.name} />
        <Row label={t('vault.viewer.type')} value={mime} />
        <Row label={t('vault.viewer.size')} value={formatBytes(d.size)} />
      </Section>
      {d.skipped ? (
        <p className="text-sm text-gray-500">{t('vault.viewer.tooLarge')}</p>
      ) : bytes ? (
        <>
          <button
            type="button"
            onClick={download}
            className="inline-flex items-center gap-2 px-3 py-2 text-sm text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 mb-3"
          >
            <Download size={14} />{t('vault.viewer.download')}
          </button>
          {preview}
        </>
      ) : <p className="text-sm text-gray-500">{t('vault.viewer.noContent')}</p>}
    </>
  );
}

export function RunViewer({ doc }) {
  const { t } = useI18n();
  const [showLog, setShowLog] = useState(false);
  const d = doc.data || {};
  const payload = d.payload || {};
  const output = payload.output ?? payload.response ?? payload.result ?? d.output ?? d.response;
  const log = typeof d.log === 'string' ? d.log : '';
  return (
    <>
      <Section>
        <Row label={t('vault.viewer.status')} value={d.status} />
        <Row label={t('vault.viewer.agent')} value={d.agent_id || d.agent_name || d.agent} />
        <Row label={t('vault.viewer.entity')} value={d.entity_id || d.flow_id || d.team_id || d.loop_id || d.scenario_id} />
        <Row label={t('vault.viewer.started')} value={formatWhen(d.started_at || d.created_at)} />
        <Row label={t('vault.viewer.finished')} value={formatWhen(d.finished_at || d.completed_at || d.updated_at)} />
      </Section>
      {output !== undefined && <Section title={t('vault.viewer.output')}><Text>{asText(output)}</Text></Section>}
      {output === undefined && d.children !== undefined && <Section><JsonTree value={d.children} label="children" /></Section>}
      {log && (
        <Section title={t('vault.viewer.log')}>
          <button type="button" onClick={() => setShowLog((v) => !v)} className="text-sm text-indigo-600 hover:underline mb-1">
            {showLog ? t('vault.viewer.hideLog') : t('vault.viewer.showLog')}
          </button>
          {showLog && <Text>{log}</Text>}
        </Section>
      )}
    </>
  );
}

export function GenericViewer({ doc }) {
  return <JsonTree value={doc.data ?? {}} />;
}

const BY_KIND = {
  chat: ChatViewer,
  assistant: ChatViewer,
  memory: MemoryViewer,
  agent: AgentViewer,
  skill: SkillViewer,
  file: FileViewer,
  run: RunViewer,
  entity_run: RunViewer,
};

export default function RecordViewer({ doc }) {
  const Viewer = BY_KIND[doc.kind] || GenericViewer;
  return <Viewer doc={doc} />;
}
