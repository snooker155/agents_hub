import { useI18n } from '../../i18n';

// A connector lives in the workspace that defines it; the default
// workspace's definition is visible everywhere else (connectors/channels/
// store.py, docs/workspaces.md). Every card on the Connectors page that
// reads its config from that store (credential connectors, chat channels,
// Telegram) shares this badge plus the "define here" / "remove, use the
// default's" switch, so the three stay in sync by construction.

/** "Defined in this workspace" or "From the default workspace", from the
 * GET payload's `source` field. Renders nothing until that field arrives. */
export function ConnectorSourceBadge({ source }) {
  const { t } = useI18n();
  if (source !== 'here' && source !== 'default') return null;
  const isHere = source === 'here';
  return (
    <span
      data-testid="connector-source-badge"
      className={`text-xs font-medium px-2 py-0.5 rounded border ${
        isHere
          ? 'bg-indigo-50 text-indigo-700 border-indigo-200'
          : 'bg-gray-50 text-gray-500 border-gray-200'
      }`}
    >
      {isHere ? t('connectors.source.here') : t('connectors.source.default')}
    </span>
  );
}

/** Only meaningful from the default workspace's own GET, whose payload
 * carries `defined_in`: every other workspace that keeps its own copy. */
export function ConnectorDefinedIn({ definedIn }) {
  const { t } = useI18n();
  const others = (definedIn || []).filter((w) => w !== 'default');
  if (others.length === 0) return null;
  return (
    <p className="text-xs text-gray-500" data-testid="connector-defined-in">
      {t('connectors.source.alsoDefinedIn', { list: others.join(', ') })}
    </p>
  );
}

/**
 * The one action a non default workspace's card offers: "Define for this
 * workspace" while it still inherits the default's (switches the form to
 * editable; saving is what actually creates the workspace's own copy), or
 * "Remove, use the default's" once it has one (a DELETE, confirmed first).
 */
export function ConnectorSourceActions({ payload, isDefaultWorkspace, editing, onDefine, onRemove, busy }) {
  const { t } = useI18n();
  if (!payload || isDefaultWorkspace) return null;
  if (payload.source === 'default' && !editing) {
    return (
      <button
        type="button"
        onClick={onDefine}
        data-testid="connector-define-here"
        className="flex items-center gap-1.5 border border-indigo-300 text-indigo-700 hover:bg-indigo-50 px-3 py-1.5 rounded-lg text-sm font-medium"
      >
        {t('connectors.source.defineForWorkspace')}
      </button>
    );
  }
  if (payload.source === 'here') {
    return (
      <button
        type="button"
        onClick={onRemove}
        disabled={busy}
        data-testid="connector-remove-here"
        className="flex items-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
      >
        {t('connectors.source.removeUseDefault')}
      </button>
    );
  }
  return null;
}
