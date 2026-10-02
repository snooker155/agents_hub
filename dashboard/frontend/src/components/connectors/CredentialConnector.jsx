import { useCallback, useEffect, useState } from 'react';
import {
  listConnectors, getConnectorConfig, updateConnectorConfig, testConnector,
} from '../../api';
import { SectionCard } from '../settingsUi';
import { useI18n } from '../../i18n';
import PageLoader from '../PageLoader';
import { ConfigForm } from './ChannelConnector';

// A connector that is only credentials and a Test button: Jira, Linear,
// Google, Microsoft, Notion, Confluence. The backend describes its fields
// (GET /api/connectors); `children` renders whatever extra the connector
// has (an OAuth button, a project picker) and receives the loaded payload.

export default function CredentialConnector({ name, children, title, intro }) {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [spec, setSpec] = useState(null);
  const [payload, setPayload] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [specs, cfg] = await Promise.all([listConnectors(), getConnectorConfig(name)]);
      setSpec((specs.data || []).find((s) => s.name === name) || { name, fields: [] });
      setPayload(cfg.data);
    } catch (e) {
      setError(`${t('connectors.channels.loadFailed')}: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, [name, t]);

  useEffect(() => { load(); }, [load]);

  const save = async (body) => {
    setSaving(true);
    setError('');
    setTestResult(null);
    try {
      const { data } = await updateConnectorConfig(name, body);
      setPayload(data);
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
      const { data } = await testConnector(name);
      setTestResult(data);
    } catch (e) {
      setTestResult({ ok: false, error: e.response?.data?.detail || e.message });
    } finally {
      setTesting(false);
    }
  };

  if (loading) return <PageLoader size="sm" />;

  return (
    <SectionCard
      title={title || t(`connectors.credentials.title.${name}`)}
      actions={payload?.configured ? (
        <span className="text-xs font-medium bg-green-50 text-green-700 border border-green-200 px-2 py-0.5 rounded">
          {t('connectors.configured')}
        </span>
      ) : (
        <span className="text-xs font-medium bg-gray-50 text-gray-500 border border-gray-200 px-2 py-0.5 rounded">
          {t('connectors.notConfigured')}
        </span>
      )}
    >
      <p className="text-sm text-gray-600">{intro || t(`connectors.credentials.intro.${name}`)}</p>
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
      />
      {typeof children === 'function' ? children({ payload, reload: load }) : children}
    </SectionCard>
  );
}
