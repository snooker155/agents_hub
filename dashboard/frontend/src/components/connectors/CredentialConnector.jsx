import { useCallback, useEffect, useState } from 'react';
import {
  listConnectors, getConnectorConfig, updateConnectorConfig, deleteConnectorConfig, testConnector,
} from '../../api';
import { SectionCard } from '../settingsUi';
import { useI18n } from '../../i18n';
import { useWorkspace } from '../workspace';
import PageLoader from '../PageLoader';
import { ConfigForm } from './ChannelConnector';
import { ConnectorSourceBadge, ConnectorDefinedIn, ConnectorSourceActions } from './ConnectorSource';

// A connector that is only credentials and a Test button: Jira, Linear,
// Google, Microsoft, Notion, Confluence. The backend describes its fields
// (GET /api/connectors); `children` renders whatever extra the connector
// has (an OAuth button, a project picker) and receives the loaded payload.
//
// Scoped per workspace like every other connector (connectors/channels/
// store.py): the default workspace's definition works everywhere, another
// workspace's own definition works only there. The GET payload's `source`
// says which one this is; a non default workspace that still inherits the
// default's shows its fields read only, with "Define for this workspace" as
// the only way in (ConnectorSourceActions, ./ConnectorSource.jsx).

export default function CredentialConnector({ name, children, title, intro }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || 'default';
  const isDefaultWorkspace = workspace === 'default';
  // `reloading` is a user-triggered reload (children call `reload`); the first
  // load and a change of connector or workspace show up as `loadedKey` lagging.
  const loadKey = `${name}|${workspace}`;
  const [reloading, setReloading] = useState(false);
  const [loadedKey, setLoadedKey] = useState(null);
  const loading = reloading || loadedKey !== loadKey;
  const [spec, setSpec] = useState(null);
  const [payload, setPayload] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState('');
  // "Define for this workspace" was clicked: the inherited, read only form
  // becomes editable, and a save is what creates this workspace's own copy.
  const [editing, setEditing] = useState(false);
  // Another connector or workspace starts read only again.
  const [editKey, setEditKey] = useState(loadKey);
  if (editKey !== loadKey) {
    setEditKey(loadKey);
    setEditing(false);
  }

  // Promise chain rather than try/await: the lint rule cannot tell that no
  // state is set before the first await of an async function with a catch.
  const fetchConfig = useCallback(() => {
    Promise.all([listConnectors(), getConnectorConfig(name, workspace)])
      .then(([specs, cfg]) => {
        setError('');
        setSpec((specs.data || []).find((s) => s.name === name) || { name, fields: [] });
        setPayload(cfg.data);
      })
      .catch((e) => {
        setError(`${t('connectors.channels.loadFailed')}: ${e.response?.data?.detail || e.message}`);
      })
      .finally(() => {
        setReloading(false);
        setLoadedKey(`${name}|${workspace}`);
      });
  }, [name, workspace, t]);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

  // Handed to children: shows the loader again and starts over.
  const load = () => {
    setReloading(true);
    setError('');
    setEditing(false);
    fetchConfig();
  };

  const save = async (body) => {
    setSaving(true);
    setError('');
    setTestResult(null);
    try {
      const { data } = await updateConnectorConfig(name, body, workspace);
      setPayload(data);
      setEditing(false);
    } catch (e) {
      setError(`${t('settings.errors.save')}: ${e.response?.data?.detail || e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const { data } = await testConnector(name, workspace);
      setTestResult(data);
    } catch (e) {
      setTestResult({ ok: false, error: e.response?.data?.detail || e.message });
    } finally {
      setTesting(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(t('connectors.source.confirmRemove'))) return;
    setSaving(true);
    setError('');
    try {
      const { data } = await deleteConnectorConfig(name, workspace);
      setPayload(data);
    } catch (e) {
      setError(`${t('settings.errors.save')}: ${e.response?.data?.detail || e.message}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <PageLoader size="sm" />;

  const readOnly = !isDefaultWorkspace && payload?.source === 'default' && !editing;

  return (
    <SectionCard
      title={title || t(`connectors.credentials.title.${name}`)}
      actions={(
        <div className="flex items-center gap-2">
          <ConnectorSourceBadge source={payload?.source} />
          {payload?.configured ? (
            <span className="text-xs font-medium bg-green-50 text-green-700 border border-green-200 px-2 py-0.5 rounded">
              {t('connectors.configured')}
            </span>
          ) : (
            <span className="text-xs font-medium bg-gray-50 text-gray-500 border border-gray-200 px-2 py-0.5 rounded">
              {t('connectors.notConfigured')}
            </span>
          )}
        </div>
      )}
    >
      <p className="text-sm text-gray-600">{intro || t(`connectors.credentials.intro.${name}`)}</p>
      {isDefaultWorkspace ? (
        <ConnectorDefinedIn definedIn={payload?.defined_in} />
      ) : (
        <ConnectorSourceActions
          payload={payload}
          isDefaultWorkspace={isDefaultWorkspace}
          editing={editing}
          onDefine={() => setEditing(true)}
          onRemove={remove}
          busy={saving}
        />
      )}
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}
      <ConfigForm
        name={name}
        fields={spec?.fields || []}
        config={payload?.config}
        onSave={(config) => save({ config })}
        onClear={(keys) => save({ clear: keys })}
        onTest={test}
        saving={saving}
        testing={testing}
        testResult={testResult}
        t={t}
        readOnly={readOnly}
        workspace={workspace}
      />
      {typeof children === 'function'
        ? children({ payload, reload: load, workspace, isDefaultWorkspace, readOnly })
        : children}
    </SectionCard>
  );
}
