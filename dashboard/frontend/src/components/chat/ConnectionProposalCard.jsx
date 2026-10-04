/**
 * The card for a `propose_connection` tool approval (docs/hooks.md "In chat",
 * connection-proposals-contract.md): an agent asks to wire up a connector, a
 * chat channel, an MCP server, a database, a watcher or a workspace secret.
 * The agent fills only the non-secret fields; the person edits them, types
 * the secrets here (they never pass through the model), and presses Connect.
 * The backend applies the change as the person, runs a test, and the turn
 * continues with the outcome. Deny works exactly like ToolApprovalCard's.
 */
import { useState } from 'react';
import { Plug } from 'lucide-react';
import { useI18n } from '../../i18n';
import { decideToolApproval } from '../../api/toolApprovals';
import { applyConnectionProposal } from '../../api/connectionProposals';

const STATUS_CLASSES = {
  pending: 'bg-amber-100 text-amber-800',
  approved: 'bg-emerald-100 text-emerald-700',
  denied: 'bg-red-100 text-red-700',
  expired: 'bg-gray-100 text-gray-600',
  cancelled: 'bg-gray-100 text-gray-600',
};

// The field input style: the same compact classes ToolApprovalCard's own
// note textarea uses, not the larger Settings-page inputs: this card lives
// under a chat bubble, not on a form page.
const fieldInputCls = 'mt-1 w-full rounded border border-gray-200 bg-white px-2 py-1 text-xs text-gray-800 focus:border-indigo-300 focus:outline-none';

function clockTime(iso) {
  if (!iso) return '';
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? '' : when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function errorKey(err) {
  const status = err?.response?.status;
  if (status === 403) return 'toolApproval.errors.forbidden';
  if (status === 409) return 'toolApproval.errors.gone';
  return 'toolApproval.errors.failed';
}

// 400s carry the backend's own message, a string or `{message}`; everything
// else falls back to the same wording ToolApprovalCard uses for its errors.
function describeError(err, t) {
  if (err?.response?.status === 400) {
    const detail = err.response.data?.detail;
    if (typeof detail === 'string' && detail) return detail;
    if (detail && typeof detail === 'object' && typeof detail.message === 'string' && detail.message) return detail.message;
  }
  return t(errorKey(err));
}

// "headers.Authorization" -> "Headers Authorization", "api_token" -> "Api
// token": the field keys are not catalogued anywhere (any connector, any
// target can propose one), so the label is computed rather than looked up.
function fieldLabel(key) {
  const spaced = String(key || '').replace(/[._:]+/g, ' ').trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : key;
}

function isNonSecretFilled(field, value) {
  if (field.kind === 'bool') return true;
  return String(value ?? '').trim().length > 0;
}

function isSecretFilled(field, secretValue) {
  if (field.has_value) return true;
  return String(secretValue ?? '').trim().length > 0;
}

function toPayloadValue(field, value) {
  if (field.kind === 'number') return value === '' || value == null ? value : Number(value);
  if (field.kind === 'bool') return Boolean(value);
  if (field.kind === 'list') return String(value || '').split('\n').map((s) => s.trim()).filter(Boolean);
  return value;
}

function initialValues(fields) {
  const out = {};
  fields.forEach((f) => {
    if (f.secret) return;
    if (f.kind === 'bool') out[f.key] = Boolean(f.value);
    else if (f.kind === 'list') out[f.key] = Array.isArray(f.value) ? f.value.join('\n') : (f.value || '');
    else out[f.key] = f.value ?? '';
  });
  return out;
}

function initialSecrets(fields) {
  const out = {};
  fields.forEach((f) => { if (f.secret) out[f.key] = ''; });
  return out;
}

function FieldRow({ field, value, onChange, secretValue, onSecretChange, t }) {
  const label = fieldLabel(field.key);
  const mark = field.required ? <span className="text-red-500"> *</span> : null;

  if (field.secret) {
    return (
      <div>
        <label className="block text-xs font-medium text-gray-700">{label}{mark}</label>
        <input
          type="password"
          autoComplete="new-password"
          className={fieldInputCls}
          value={secretValue ?? ''}
          onChange={(e) => onSecretChange(e.target.value)}
          placeholder={field.has_value ? t('connectionProposal.secretKeepHint') : (field.placeholder || '')}
        />
      </div>
    );
  }
  if (field.kind === 'bool') {
    return (
      <label className="flex items-center gap-2 text-xs font-medium text-gray-700">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        {label}{mark}
      </label>
    );
  }
  if (field.kind === 'select') {
    return (
      <div>
        <label className="block text-xs font-medium text-gray-700">{label}{mark}</label>
        <select className={fieldInputCls} value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
          {(field.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    );
  }
  if (field.kind === 'textarea' || field.kind === 'list') {
    return (
      <div>
        <label className="block text-xs font-medium text-gray-700">{label}{mark}</label>
        <textarea
          rows={3}
          className={fieldInputCls}
          value={value ?? ''}
          placeholder={field.kind === 'list' ? t('connectionProposal.listPlaceholder') : (field.placeholder || '')}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  }
  return (
    <div>
      <label className="block text-xs font-medium text-gray-700">{label}{mark}</label>
      <input
        type={field.kind === 'number' ? 'number' : 'text'}
        className={fieldInputCls}
        value={value ?? ''}
        placeholder={field.placeholder || ''}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

export function ConnectionProposalCard({ approval, live = true, onSettled }) {
  const { t } = useI18n();
  const proposal = approval.input || {};
  const fields = Array.isArray(proposal.fields) ? proposal.fields : [];
  const [values, setValues] = useState(() => initialValues(fields));
  const [secrets, setSecrets] = useState(() => initialSecrets(fields));
  const [note, setNote] = useState('');
  const [sending, setSending] = useState('');
  const [answered, setAnswered] = useState(null); // deny's optimistic result
  const [applied, setApplied] = useState(null); // connect's { approval, outcome }
  const [error, setError] = useState('');

  const local = applied?.approval || answered || null;
  const status = approval.status !== 'pending' ? approval.status : (local?.status || 'pending');
  const settled = local && approval.status === 'pending' ? local : approval;
  const open = status === 'pending' && live;
  const until = clockTime(approval.expires_at);

  const canConnect = fields.every((f) => {
    if (!f.required) return true;
    return f.secret ? isSecretFilled(f, secrets[f.key]) : isNonSecretFilled(f, values[f.key]);
  });

  const connect = async () => {
    setSending('connect');
    setError('');
    try {
      const payloadValues = {};
      const payloadSecrets = {};
      fields.forEach((f) => {
        if (f.secret) {
          const v = String(secrets[f.key] || '').trim();
          if (v) payloadSecrets[f.key] = v;
        } else {
          payloadValues[f.key] = toPayloadValue(f, values[f.key]);
        }
      });
      const { data } = await applyConnectionProposal(approval.approval_id, { values: payloadValues, secrets: payloadSecrets });
      setApplied(data || { approval: { status: 'approved' }, outcome: null });
      onSettled?.();
    } catch (err) {
      setError(describeError(err, t));
    } finally {
      setSending('');
    }
  };

  const deny = async () => {
    setSending('deny');
    setError('');
    try {
      const { data } = await decideToolApproval(approval.approval_id, 'deny', note.trim());
      setAnswered(data?.approval || { status: 'denied' });
      onSettled?.();
    } catch (err) {
      setError(describeError(err, t));
    } finally {
      setSending('');
    }
  };

  return (
    <div
      className="mt-2 rounded-lg border border-indigo-200 bg-indigo-50/50 px-3 py-2.5 text-sm"
      data-testid="connection-proposal-card"
      role="group"
      aria-label={t('connectionProposal.title')}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Plug className="h-4 w-4 flex-shrink-0 text-indigo-600" />
        <span className="font-semibold text-indigo-800">{t('connectionProposal.title')}</span>
        {proposal.title ? <span className="font-semibold text-gray-800">{proposal.title}</span> : null}
        {proposal.kind ? (
          <span className="rounded bg-white/70 px-1.5 py-0.5 text-[10px] font-medium text-gray-600">
            {t(`connectionProposal.kind.${proposal.kind}`, { defaultValue: proposal.kind })}
          </span>
        ) : null}
        <span className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_CLASSES[status] || STATUS_CLASSES.expired}`}>
          {t(`toolApproval.status.${status}`)}
        </span>
      </div>

      {approval.reason ? (
        <p className="mt-1 text-xs text-gray-600">{approval.reason}</p>
      ) : null}

      {(proposal.warnings || []).length > 0 ? (
        <ul className="mt-1.5 space-y-0.5 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800" data-testid="connection-proposal-warnings">
          {proposal.warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      ) : null}

      {proposal.replaces ? (
        <p className="mt-1.5 text-[11px] text-amber-700">
          {t('connectionProposal.replaces', { title: proposal.title || proposal.target || '' })}
        </p>
      ) : null}

      {open ? (
        <>
          <div className="mt-2 space-y-2">
            {fields.map((f) => (
              <FieldRow
                key={f.key}
                field={f}
                t={t}
                value={values[f.key]}
                onChange={(v) => setValues((prev) => ({ ...prev, [f.key]: v }))}
                secretValue={secrets[f.key]}
                onSecretChange={(v) => setSecrets((prev) => ({ ...prev, [f.key]: v }))}
              />
            ))}
          </div>
          {fields.some((f) => f.secret) ? (
            <p className="mt-1 text-[11px] text-gray-500">{t('connectionProposal.secretsHint')}</p>
          ) : null}
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            maxLength={2000}
            placeholder={t('toolApproval.notePlaceholder')}
            aria-label={t('toolApproval.noteLabel')}
            className="mt-2 w-full resize-y rounded border border-gray-200 bg-white px-2 py-1 text-xs text-gray-800 focus:border-indigo-300 focus:outline-none"
          />
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={connect}
              disabled={!!sending || !canConnect}
              className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {t('connectionProposal.connect')}
            </button>
            <button
              type="button"
              onClick={deny}
              disabled={!!sending}
              className="rounded border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              {t('toolApproval.deny')}
            </button>
            {until ? <span className="text-[11px] text-gray-500">{t('toolApproval.waitsUntil', { time: until })}</span> : null}
          </div>
        </>
      ) : null}

      {applied?.outcome ? (
        <p className="mt-1.5 text-[11px] text-emerald-700" data-testid="connection-proposal-outcome">
          {applied.outcome.summary}
          {applied.outcome.href ? (
            <>
              {' · '}
              <a href={applied.outcome.href} className="underline">{t('connectionProposal.openLink')}</a>
            </>
          ) : null}
        </p>
      ) : null}

      {status !== 'pending' && (settled.decided_by_name || settled.note) ? (
        <p className="mt-1 text-[11px] text-gray-500" data-testid="connection-proposal-outcome-note">
          {settled.decided_by_name ? t('toolApproval.decidedBy', { name: settled.decided_by_name }) : null}
          {settled.decided_by_name && settled.note ? ' · ' : null}
          {settled.note ? t('toolApproval.noteShown', { note: settled.note }) : null}
        </p>
      ) : null}

      {error ? <p className="mt-1 text-[11px] text-red-600" role="alert">{error}</p> : null}
    </div>
  );
}

export default ConnectionProposalCard;
