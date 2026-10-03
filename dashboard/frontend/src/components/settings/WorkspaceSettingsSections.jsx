// The workspace-scoped settings: provider keys, local model servers, agent
// execution, RAG, observability and logging. They are overrides stored in the
// workspace's metadata, so the same fields are drawn on two pages: Settings,
// for the workspace picked in the header, and the Settings tab of a workspace,
// for that workspace. Both render these sections over one useWorkspaceSettings
// state (useWorkspaceSettings.js), so a field added here shows up in both places.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getWorkspaceWebPolicy, updateWorkspaceWebPolicy } from '../../api';
import { RefreshCw, CheckCircle, AlertCircle, Wifi, Lock, Save } from 'lucide-react';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';
import ToolPolicySettings from './ToolPolicySettings';
import LoopSettingsWorkspace from './LoopSettingsWorkspace';

// Brand names stay as they are; only the descriptive rows carry a key.
const VECTOR_DBS = [
  { value: 'none',     labelKey: 'settings.vectorDbs.none' },
  { value: 'chroma',   label: 'ChromaDB' },
  { value: 'pinecone', label: 'Pinecone' },
  { value: 'qdrant',   label: 'Qdrant' },
];

const EMBEDDING_PROVIDERS = [
  { value: 'none',                  labelKey: 'settings.embeddingProviders.none' },
  { value: 'openai',                label: 'OpenAI' },
  { value: 'sentence-transformers', labelKey: 'settings.embeddingProviders.sentenceTransformers' },
  { value: 'ollama',                labelKey: 'settings.embeddingProviders.ollama' },
  { value: 'google',                labelKey: 'settings.embeddingProviders.google' },
];

// Mirrors the Literal in common/config.py — common.logging_config applies the
// chosen level to the backend process and to every agent subprocess it spawns.
const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

const optionLabel = (o, t) => (o.labelKey ? t(o.labelKey) : o.label);

const INSTALL_HINTS = {
  chroma: 'pip install chromadb',
  pinecone: 'pip install pinecone-client',
  qdrant: 'pip install qdrant-client',
  openai: 'pip install openai',
  'sentence-transformers': 'pip install sentence-transformers',
  ollama: 'settings.noExtraPackage',
  google: 'pip install google-generativeai',
};

// Most hints are literal pip commands; the Ollama one is prose, so it is a key.
const installHint = (key, t) => {
  const hint = INSTALL_HINTS[key];
  return hint && hint.startsWith('settings.') ? t(hint) : hint;
};

// ── Source badge — shows where a setting value comes from ─────────────────────

function SourceBadge({ fieldName, wsOverrides, envDefinedFields = [] }) {
  const { t } = useI18n();
  const hasWsOverride = Boolean(
    wsOverrides
    && Object.prototype.hasOwnProperty.call(wsOverrides, fieldName)
    && String(wsOverrides[fieldName] ?? '').trim() !== ''
  );
  if (hasWsOverride) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-50 border border-green-200 rounded-full px-1.5 py-0.5">
        <CheckCircle className="w-2.5 h-2.5" /> {t('settings.workspace')}
      </span>
    );
  }
  if (envDefinedFields.includes(fieldName)) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-full px-1.5 py-0.5">
        <Lock className="w-2.5 h-2.5" /> {t('settings.fromEnv')}
      </span>
    );
  }
  return null;
}

export function ProviderStatusBadge({ status, testing }) {
  const { t } = useI18n();
  if (testing) return <span className="flex items-center gap-1 text-xs text-gray-500"><RefreshCw className="w-3 h-3 animate-spin" /> {t('settings.testing')}</span>;
  if (!status) return null;
  if (status.ok) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full font-medium">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
        {t('settings.available')}
        {status.models?.length > 0 && <span className="opacity-70">· {t('settings.modelCount', { count: status.models.length })}</span>}
        {status.latency_ms && <span className="opacity-70">· {status.latency_ms}ms</span>}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 px-2 py-0.5 rounded-full font-medium" title={status.error}>
      <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
      {t('settings.unavailable')} · <span className="opacity-70 max-w-40 truncate">{status.error}</span>
    </span>
  );
}

function ProviderHeader({ status, testing, onTest }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ProviderStatusBadge status={status} testing={testing} />
      <button type="button" onClick={onTest} disabled={testing}
        className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-gray-300 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors">
        {testing ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Wifi className="w-3 h-3" />}
        {t('settings.test')}
      </button>
    </div>
  );
}

// ── Sections ──────────────────────────────────────────────────────────────────

/** The label row of a field: its name and where its value comes from. */
function FieldLabel({ field, s, children }) {
  return (
    <div className="flex items-center gap-2 mb-1">
      <label className="text-sm font-medium text-gray-700">{children}</label>
      <SourceBadge fieldName={field} {...s.badgeProps} />
    </div>
  );
}

function SecretInput({ field, s }) {
  const { t } = useI18n();
  return (
    <input type="password" value={s.g[field] || ''} onChange={e => s.setG(field, e.target.value)}
      placeholder={t('settings.leaveEmptyToInheritThe')}
      className={inputCls} autoComplete="new-password" />
  );
}

function KeyLabel({ field, s, label }) {
  const current = s.masked[`${field}_masked`];
  return (
    <FieldLabel field={field} s={s}>
      {label} {current && <CurrentValue value={current} />}
    </FieldLabel>
  );
}

function CurrentValue({ value }) {
  const { t } = useI18n();
  return <span className="text-gray-400 font-normal">({t('settings.current')}: {value})</span>;
}

export function ProvidersSection({ s }) {
  const { t } = useI18n();
  const header = (id) => (
    <ProviderHeader status={s.providerStatus[id]} testing={s.providerTesting[id]} onTest={() => s.testProvider(id)} />
  );
  return (
    <div className="space-y-5">
      <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-xs text-gray-500">
        {t('settings.providersIntroBefore')} <span className="font-medium text-gray-700">{t('settings.models')}</span> {t('settings.providersIntroAfter')}
      </div>
      <SectionCard title={t('settings.openai')} actions={header('openai')}>
        <div>
          <KeyLabel field="openai_api_key" s={s} label={t('settings.apiKeyLabel')} />
          <SecretInput field="openai_api_key" s={s} />
        </div>
        <div>
          <FieldLabel field="openai_base_url" s={s}>{t('settings.baseUrlOverride')}</FieldLabel>
          <p className="text-xs text-gray-500 mb-1">{t('settings.useThisToPointTo')}</p>
          <input type="text" value={s.g.openai_base_url || ''} onChange={e => s.setG('openai_base_url', e.target.value)}
            placeholder={t('settings.httpsApiOpenaiComV1')}
            className={inputCls} />
        </div>
      </SectionCard>

      <SectionCard title={t('settings.anthropicClaude')} actions={header('anthropic')}>
        <div>
          <KeyLabel field="anthropic_api_key" s={s} label={t('settings.apiKeyLabel')} />
          <SecretInput field="anthropic_api_key" s={s} />
        </div>
      </SectionCard>

      <SectionCard title={t('settings.googleGemini')} actions={header('google')}>
        <div>
          <KeyLabel field="google_api_key" s={s} label={t('settings.apiKeyLabel')} />
          <SecretInput field="google_api_key" s={s} />
        </div>
      </SectionCard>
    </div>
  );
}

export function LocalServersSection({ s }) {
  const { t } = useI18n();
  const server = (id, title, intro, hint, placeholder) => (
    <SectionCard
      title={title}
      actions={<ProviderHeader status={s.providerStatus[id]} testing={s.providerTesting[id]} onTest={() => s.testProvider(id)} />}
    >
      <p className="text-sm text-gray-600">{intro}</p>
      <div>
        <FieldLabel field={`${id}_base_url`} s={s}>{t('settings.baseUrl')}</FieldLabel>
        <p className="text-xs text-gray-500 mb-1">{hint}</p>
        <input type="text" value={s.g[`${id}_base_url`] || ''} onChange={e => s.setG(`${id}_base_url`, e.target.value)}
          placeholder={placeholder} className={inputCls} />
        {s.fetchErrors[id] && <p className="text-xs text-red-600 mt-1 flex items-center gap-1"><AlertCircle className="w-3 h-3" />{s.fetchErrors[id]}</p>}
      </div>
    </SectionCard>
  );
  return (
    <div className="space-y-5">
      <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-xs text-gray-500">
        {t('settings.localIntroBefore')} <span className="font-medium text-gray-700">{t('settings.models')}</span> {t('settings.localIntroAfter')}
      </div>
      {server(
        'ollama', t('settings.ollama'),
        <>{t('settings.ollamaIntroBefore')} <span className="font-medium text-gray-800">{t('settings.ollama')}</span>{t('settings.ollamaIntroAfter')}</>,
        t('settings.ollamaServerAddress'), 'http://localhost:11434',
      )}
      {server(
        'lmstudio', t('settings.lmStudio'),
        <><span className="font-medium text-gray-800">{t('settings.lmStudio')}</span> {t('settings.lmStudioIntro')}</>,
        t('settings.lmstudioServerAddress'), 'http://localhost:1234',
      )}
    </div>
  );
}

export function ObservabilitySection({ s }) {
  const { t } = useI18n();
  return (
    <div className="space-y-5">
      <SectionCard title={t('settings.langfuse')}>
        <p className="text-sm text-gray-600">
          {t('settings.langfuseIntroBefore')} <span className="font-medium text-gray-800">{t('settings.langfuse')}</span> {t('settings.langfuseIntroAfter')}
        </p>
        <div>
          <KeyLabel field="langfuse_secret_key" s={s} label={t('settings.secretKey')} />
          <SecretInput field="langfuse_secret_key" s={s} />
        </div>
        <div>
          <KeyLabel field="langfuse_public_key" s={s} label={t('settings.publicKey')} />
          <SecretInput field="langfuse_public_key" s={s} />
        </div>
        <div>
          <FieldLabel field="langfuse_base_url" s={s}>{t('settings.baseUrl')}</FieldLabel>
          <input type="text" value={s.g.langfuse_base_url || ''} onChange={e => s.setG('langfuse_base_url', e.target.value)}
            placeholder="https://cloud.langfuse.com"
            className={inputCls} />
        </div>
      </SectionCard>
    </div>
  );
}

export function RagSection({ s }) {
  const { t } = useI18n();
  const { g, setG } = s;
  return (
    <div className="space-y-5">
      {(g.rag_vector_db !== 'none' || g.rag_embedding_provider !== 'none') && (
        <div className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-sm ${
          g.rag_vector_db !== 'none' && g.rag_embedding_provider !== 'none'
            ? 'bg-green-50 border-green-200 text-green-800'
            : 'bg-yellow-50 border-yellow-200 text-yellow-800'
        }`}>
          {g.rag_vector_db !== 'none' && g.rag_embedding_provider !== 'none'
            ? <CheckCircle className="w-4 h-4 mt-0.5 shrink-0" />
            : <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />}
          <div>
            {g.rag_vector_db !== 'none' && g.rag_embedding_provider !== 'none'
              ? <><strong>{t('settings.ragPipelineActive')}</strong> {t('settings.embeddedWith')} <strong>{g.rag_embedding_provider}</strong>{t('settings.storedIn2')} <strong>{g.rag_vector_db}</strong>.</>
              : <>{t('settings.ragPartiallyConfigured')}</>}
          </div>
        </div>
      )}

      <SectionCard title={t('settings.vectorDatabase')}>
        <p className="text-sm text-gray-600">{t('settings.chooseWhereProcessedDocumentChunks')}</p>
        <div>
          <FieldLabel field="rag_vector_db" s={s}>{t('settings.provider')}</FieldLabel>
          <select value={g.rag_vector_db || 'none'} onChange={e => setG('rag_vector_db', e.target.value)}
            className={inputCls}>
            {VECTOR_DBS.map(d => <option key={d.value} value={d.value}>{optionLabel(d, t)}</option>)}
          </select>
          {g.rag_vector_db !== 'none' && (
            <p className="text-xs text-gray-400 mt-1">{t('settings.install')} <code className="bg-gray-100 rounded px-1 py-0.5">{installHint(g.rag_vector_db, t)}</code></p>
          )}
        </div>

        {g.rag_vector_db !== 'none' && (
          <>
            <div>
              <FieldLabel field="rag_vector_db_url" s={s}>{t('settings.serverUrl')}</FieldLabel>
              <input type="text" value={g.rag_vector_db_url || ''} onChange={e => setG('rag_vector_db_url', e.target.value)}
                placeholder={g.rag_vector_db === 'qdrant' ? 'http://localhost:6333' : g.rag_vector_db === 'pinecone' ? 'https://your-index-host.pinecone.io' : 'http://localhost:8000'}
                className={inputCls} />
            </div>
            <div>
              <FieldLabel field="rag_vector_db_collection" s={s}>{t('settings.collectionIndexName')}</FieldLabel>
              <input type="text" value={g.rag_vector_db_collection || ''} onChange={e => setG('rag_vector_db_collection', e.target.value)}
                placeholder="agents_hub_rag"
                className={inputCls} />
            </div>
            {(g.rag_vector_db === 'pinecone' || g.rag_vector_db === 'qdrant') && (
              <div>
                <KeyLabel field="rag_vector_db_api_key" s={s} label={t('settings.apiKeyLabel')} />
                <SecretInput field="rag_vector_db_api_key" s={s} />
              </div>
            )}
          </>
        )}
      </SectionCard>

      <SectionCard title={t('settings.embeddingModel')}>
        <p className="text-sm text-gray-600">{t('settings.chooseTheModelUsedTo')}</p>
        <div>
          <FieldLabel field="rag_embedding_provider" s={s}>{t('settings.provider')}</FieldLabel>
          <select value={g.rag_embedding_provider || 'none'} onChange={e => setG('rag_embedding_provider', e.target.value)}
            className={inputCls}>
            {EMBEDDING_PROVIDERS.map(p => <option key={p.value} value={p.value}>{optionLabel(p, t)}</option>)}
          </select>
          {g.rag_embedding_provider !== 'none' && (
            <p className="text-xs text-gray-400 mt-1">{t('settings.install')} <code className="bg-gray-100 rounded px-1 py-0.5">{installHint(g.rag_embedding_provider, t)}</code></p>
          )}
        </div>

        {g.rag_embedding_provider !== 'none' && (
          <>
            <div>
              <FieldLabel field="rag_embedding_model" s={s}>{t('settings.modelName')}</FieldLabel>
              <input type="text" value={g.rag_embedding_model || ''} onChange={e => setG('rag_embedding_model', e.target.value)}
                placeholder={
                  g.rag_embedding_provider === 'openai' ? 'text-embedding-3-small'
                  : g.rag_embedding_provider === 'google' ? 'text-embedding-004'
                  : g.rag_embedding_provider === 'ollama' ? 'nomic-embed-text'
                  : 'all-MiniLM-L6-v2'
                }
                className={inputCls} />
            </div>
            {(g.rag_embedding_provider === 'openai' || g.rag_embedding_provider === 'google') && (
              <div>
                <KeyLabel field="rag_embedding_api_key" s={s} label={t('settings.apiKeyLabel')} />
                <SecretInput field="rag_embedding_api_key" s={s} />
              </div>
            )}
            {g.rag_embedding_provider === 'ollama' && (
              <div>
                <FieldLabel field="rag_embedding_base_url" s={s}>{t('settings.ollamaBaseUrl')}</FieldLabel>
                <input type="text" value={g.rag_embedding_base_url || ''} onChange={e => setG('rag_embedding_base_url', e.target.value)}
                  placeholder="http://localhost:11434"
                  className={inputCls} />
              </div>
            )}
          </>
        )}
      </SectionCard>
    </div>
  );
}

export function LoggingSection({ s }) {
  const { t } = useI18n();
  return (
    <div className="space-y-5">
      <SectionCard title={t('settings.logging.title')}>
        <p className="text-sm text-gray-600">{t('settings.logging.intro')}</p>
        <div>
          <FieldLabel field="orch_log_level" s={s}>{t('settings.logLevel')}</FieldLabel>
          <select value={s.g.orch_log_level || 'INFO'} onChange={e => s.setG('orch_log_level', e.target.value)}
            className={inputCls}>
            {LOG_LEVELS.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
          <p className="text-xs text-gray-500 mt-1">{t('settings.logging.hint')}</p>
        </div>
      </SectionCard>
    </div>
  );
}

export function Toggle({ checked, disabled, onChange }) {
  return (
    <label className="inline-flex items-center cursor-pointer shrink-0">
      <input
        type="checkbox"
        className="sr-only peer"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-indigo-600 peer-disabled:opacity-50 relative transition-colors">
        <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${checked ? 'translate-x-5' : ''}`} />
      </span>
    </label>
  );
}

function WorkspaceBadge({ workspace }) {
  const { t } = useI18n();
  if (!workspace) return null;
  return (
    <span className="text-xs text-indigo-600 font-medium bg-indigo-50 border border-indigo-200 rounded-full px-2 py-0.5">
      {t('settings.workspaceBadge', { workspace })}
    </span>
  );
}

/** Where this workspace's agents run: a local subprocess or a Docker container. */
export function ExecutionModeSection({ s }) {
  const { t } = useI18n();
  const { g, setG, workspace } = s;
  return (
    <SectionCard title={t('settings.agentExecutionMode')}>
      <div>
        <div className="flex items-center gap-2 mb-1">
          <label className="text-sm font-medium text-gray-700">{t('settings.agentMode')}</label>
          <SourceBadge fieldName="agent_mode" {...s.badgeProps} />
          <WorkspaceBadge workspace={workspace} />
        </div>
        <p className="text-xs text-gray-500 mb-2">
          <strong>{t('settings.local')}</strong> {t('settings.agentsRunAsSubprocesses')} <strong>{t('settings.docker')}</strong> {t('settings.agentsRunInDocker')}
        </p>
        <div className="flex gap-4">
          {['local', 'docker'].map(mode => (
            <label key={mode} className="flex items-center gap-2 cursor-pointer">
              <input type="radio" name={`agent_mode_${workspace}`} value={mode}
                checked={(g.agent_mode || 'local') === mode}
                onChange={() => setG('agent_mode', mode)}
                className="accent-indigo-600" />
              <span className="text-sm font-medium text-gray-700 capitalize">{mode}</span>
            </label>
          ))}
        </div>
      </div>

      {/* Where a chat turn runs (docs/services.md): on a service replica, a
          process of its own, or in the backend process. Global, saved on change. */}
      <div className="mt-4 pt-4 border-t border-gray-100">
        <div className="flex items-center gap-2 mb-1">
          <label className="text-sm font-medium text-gray-700">{t('settings.chatExecution')}</label>
          <SourceBadge fieldName="chat_execution" {...s.badgeProps} />
        </div>
        <p className="text-xs text-gray-500 mb-2">
          <strong>{t('settings.chatExecutionInstances')}</strong> {t('settings.chatExecutionInstancesHint')} <strong>{t('settings.chatExecutionInprocess')}</strong> {t('settings.chatExecutionInprocessHint')}
        </p>
        <div className="flex gap-4">
          {['instances', 'inprocess'].map((mode) => (
            <label key={mode} className="flex items-center gap-2 cursor-pointer">
              <input type="radio" name="chat_execution" value={mode}
                checked={(s.globalSettings.chat_execution || 'instances') === mode}
                disabled={s.chatExecutionSaving}
                onChange={() => s.setChatExecution(mode)}
                className="accent-indigo-600" />
              <span className="text-sm font-medium text-gray-700">
                {mode === 'instances' ? t('settings.chatExecutionInstances') : t('settings.chatExecutionInprocess')}
              </span>
            </label>
          ))}
        </div>
      </div>

      {g.agent_mode === 'docker' && (
        <div className="space-y-4 mt-2 pt-4 border-t border-gray-100">
          <div>
            <FieldLabel field="agent_docker_image" s={s}>{t('settings.dockerImage')}</FieldLabel>
            <input type="text" value={g.agent_docker_image || ''} onChange={e => setG('agent_docker_image', e.target.value)}
              placeholder="agents-hub:latest"
              className={inputCls} />
          </div>
          <div>
            <FieldLabel field="agent_docker_network" s={s}>{t('settings.dockerNetwork')}</FieldLabel>
            <input type="text" value={g.agent_docker_network || ''} onChange={e => setG('agent_docker_network', e.target.value)}
              placeholder="agents_hub_default"
              className={inputCls} />
          </div>
          <div>
            <FieldLabel field="agent_docker_extra_args" s={s}>{t('settings.extraDockerArgs')}</FieldLabel>
            <input type="text" value={g.agent_docker_extra_args || ''} onChange={e => setG('agent_docker_extra_args', e.target.value)}
              placeholder="--add-host host.docker.internal:host-gateway"
              className={inputCls} />
          </div>
        </div>
      )}
    </SectionCard>
  );
}

/** The approval gate, the per-tool policy, the agent loop and the hooks. Each saves itself. */
export function ToolPolicySection({ s }) {
  const { t } = useI18n();
  const { workspace } = s;
  return (
    <SectionCard title={t('settings.toolPolicy')}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium text-gray-700">{t('settings.toolApproval')}</label>
            <WorkspaceBadge workspace={workspace} />
          </div>
          <p className="text-xs text-gray-500 mt-1">{t('settings.toolApprovalHint')}</p>
        </div>
        <Toggle checked={!!s.policy.require_tool_approval} disabled={s.policySaving} onChange={s.toggleApproval} />
      </div>
      {/* How long a call held in a chat turn waits for Approve or Deny. */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor="tool-approval-timeout" className="text-sm text-gray-700">{t('settings.toolApprovalTimeout')}</label>
        <input
          id="tool-approval-timeout"
          key={s.policy.tool_approval_timeout ?? 'default'}
          type="number"
          min={10}
          max={86400}
          defaultValue={s.policy.tool_approval_timeout ?? ''}
          placeholder="600"
          disabled={s.policySaving}
          onBlur={(e) => {
            const value = e.target.value.trim();
            if (value === String(s.policy.tool_approval_timeout ?? '')) return;
            s.savePolicy({ tool_approval_timeout: value === '' ? null : Number(value) });
          }}
          className={inputCls.replace('w-full', 'w-28')}
        />
        <p className="w-full text-xs text-gray-500">{t('settings.toolApprovalTimeoutHint')}</p>
      </div>

      {/* The workspace's per-tool permission policy and the model
          that decides "auto" calls (tools/permission_policy.py). */}
      <ToolPolicySettings workspace={workspace} />
      <LoopSettingsWorkspace workspace={workspace} />

      <div className="pt-4 border-t border-gray-100">
        <div className="flex items-center justify-between gap-2 mb-1">
          <label className="text-sm font-medium text-gray-700">{t('settings.hooks')}</label>
          <Link to="/docs/hooks" className="text-xs text-indigo-600 hover:text-indigo-800">
            {t('settings.hooksDocsLink')}
          </Link>
        </div>
        <p className="text-xs text-gray-500 mb-2">
          {t('settings.hooksHint')} <code className="bg-gray-100 rounded px-1">docs/hooks.md</code>
        </p>
        <textarea
          value={s.hooksText}
          onChange={(e) => { s.setHooksText(e.target.value); s.setHooksError(''); }}
          rows={10}
          spellCheck={false}
          placeholder={'{\n  "PreToolUse": []\n}'}
          className={`${inputCls} font-mono text-xs ${s.hooksError ? 'border-red-300' : ''}`}
        />
        {s.hooksError && <p className="mt-1 text-xs text-red-600">{s.hooksError}</p>}
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            onClick={s.saveHooks}
            disabled={s.policySaving}
            className="flex items-center gap-2 bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            <Save className="w-3.5 h-3.5" />
            {s.policySaving ? t('common.saving') : t('settings.saveHooks')}
          </button>
          {s.policySaved && (
            <span className="text-xs text-green-600 inline-flex items-center gap-1">
              <CheckCircle className="w-3.5 h-3.5" /> {t('settings.policySaved')}
            </span>
          )}
        </div>
      </div>
    </SectionCard>
  );
}

/** Which agents a task in this workspace may be handed to. */
export function TaskAssignmentSection({ s }) {
  const { t } = useI18n();
  const { g, setG, workspace } = s;
  return (
    <SectionCard title={t('settings.taskAssignment')}>
      <div>
        <FieldLabel field="task_assignment_mode" s={s}>{t('settings.assignmentMode')}</FieldLabel>
        <p className="text-xs text-gray-500 mb-2">
          <strong>{t('settings.anyAgent')}</strong>: {t('settings.tasksAssignedToAnyRegistered')}<br />
          <strong>{t('settings.runningNodesOnly')}</strong>: {t('settings.nodesOnlyHint')}
        </p>
        <div className="flex gap-4">
          {[
            { value: 'any', label: t('settings.anyAgent') },
            { value: 'nodes_only', label: t('settings.runningNodesOnly') },
          ].map(opt => (
            <label key={opt.value} className="flex items-center gap-2 cursor-pointer">
              <input type="radio" name={`task_assignment_mode_${workspace}`} value={opt.value}
                checked={(g.task_assignment_mode || 'any') === opt.value}
                onChange={() => setG('task_assignment_mode', opt.value)}
                className="accent-indigo-600" />
              <span className="text-sm font-medium text-gray-700">{opt.label}</span>
            </label>
          ))}
        </div>
      </div>
    </SectionCard>
  );
}

/**
 * The tool capability guard (docs/tools-and-capabilities.md): whether a tool
 * set that composes into the lethal trifecta is refused or only flagged, and
 * whether a per-agent override needs a no-network container to be honoured
 * when the agent is built. Machine-wide .env settings, so the Settings page
 * alone shows them, next to the streaming switch.
 */
export function CapabilityGuardSection({ s }) {
  const { t } = useI18n();
  const mode = s.globalSettings.capability_guard || 'block';
  const modes = [
    { value: 'block', label: t('settings.capabilityGuardBlock'), hint: t('settings.capabilityGuardBlockHint') },
    { value: 'warn', label: t('settings.capabilityGuardWarn'), hint: t('settings.capabilityGuardWarnHint') },
    { value: 'off', label: t('settings.capabilityGuardOff'), hint: t('settings.capabilityGuardOffHint') },
  ];
  return (
    <SectionCard title={t('settings.capabilityGuard')}>
      <p className="text-xs text-gray-500">{t('settings.capabilityGuardIntro')}</p>
      <div className="space-y-2">
        {modes.map(opt => (
          <label key={opt.value} className="flex items-start gap-2 cursor-pointer">
            <input type="radio" name="capability_guard" value={opt.value}
              checked={mode === opt.value}
              disabled={s.capabilityGuardSaving}
              onChange={() => s.setCapabilityGuardMode(opt.value)}
              className="accent-indigo-600 mt-1" />
            <span>
              <span className="block text-sm font-medium text-gray-700">{opt.label}</span>
              <span className="block text-xs text-gray-500">{opt.hint}</span>
            </span>
          </label>
        ))}
      </div>
      <div className="flex items-center justify-between gap-3 pt-3 border-t border-gray-100">
        <div>
          <label className="text-sm font-medium text-gray-700">{t('settings.overrideRequiresContainer')}</label>
          <p className="text-xs text-gray-500 mt-1">{t('settings.overrideRequiresContainerHint')}</p>
        </div>
        <Toggle checked={!!s.globalSettings.capability_override_requires_container} disabled={s.capabilityGuardSaving} onChange={s.toggleOverrideRequiresContainer} />
      </div>
      <p className="text-xs text-gray-400">
        {t('settings.globalSettingWrittenTo')} <code className="bg-gray-100 rounded px-1">.env</code> {t('settings.as')}
        <code className="bg-gray-100 rounded px-1 ml-1">CAPABILITY_GUARD</code>, <code className="bg-gray-100 rounded px-1">CAPABILITY_OVERRIDE_REQUIRES_CONTAINER</code>
      </p>
    </SectionCard>
  );
}

/**
 * Code execution (tools/run_code.py, sandbox/registry.py): which sandbox a
 * snippet runs in when the run's environment names none, and whether a
 * snippet may run as a plain local process when docker is down. Both the
 * agents' run_code tool and the chat Code panel's Run follow it. Applied at
 * once in the backend, so no restart.
 */
export function CodeRunnerSection({ s }) {
  const { t } = useI18n();
  const g = s.globalSettings;
  const provider = g.code_runner_provider || 'docker';
  const providers = [
    { value: 'docker', label: t('settings.codeRunnerDocker'), hint: t('settings.codeRunnerDockerHint') },
    { value: 'local', label: t('settings.codeRunnerLocal'), hint: t('settings.codeRunnerLocalHint') },
    { value: 'e2b', label: t('settings.codeRunnerE2b'), hint: t('settings.codeRunnerE2bHint') },
    { value: 'modal', label: t('settings.codeRunnerModal'), hint: t('settings.codeRunnerModalHint') },
  ];
  const dockerUp = !!g.code_runner_docker_available;
  return (
    <SectionCard title={t('settings.codeRunner')}>
      <p className="text-xs text-gray-500">{t('settings.codeRunnerIntro')}</p>
      <div className="space-y-2" data-testid="code-runner-provider">
        {providers.map(opt => (
          <label key={opt.value} className="flex items-start gap-2 cursor-pointer">
            <input type="radio" name="code_runner_provider" value={opt.value}
              checked={provider === opt.value}
              disabled={s.codeRunnerSaving}
              onChange={() => s.setCodeRunnerProvider(opt.value)}
              className="accent-indigo-600 mt-1" />
            <span>
              <span className="block text-sm font-medium text-gray-700">{opt.label}</span>
              <span className="block text-xs text-gray-500">{opt.hint}</span>
            </span>
          </label>
        ))}
      </div>
      <p className={`text-xs ${dockerUp ? 'text-green-700' : 'text-amber-700'}`} data-testid="code-runner-docker">
        {dockerUp ? t('settings.codeRunnerDockerUp') : t('settings.codeRunnerDockerDown', { reason: g.code_runner_docker_reason || '' })}
      </p>
      <div className="flex items-center justify-between gap-3 pt-3 border-t border-gray-100">
        <div>
          <label className="text-sm font-medium text-gray-700">{t('settings.codeRunnerFallback')}</label>
          <p className="text-xs text-gray-500 mt-1">{t('settings.codeRunnerFallbackHint')}</p>
        </div>
        <Toggle checked={g.code_runner_fallback === 'local'} disabled={s.codeRunnerSaving} onChange={s.toggleCodeRunnerFallback} />
      </div>
      <p className="text-xs text-gray-400">
        {t('settings.codeRunnerApplied')} {t('settings.globalSettingWrittenTo')} <code className="bg-gray-100 rounded px-1">.env</code> {t('settings.as')}
        <code className="bg-gray-100 rounded px-1 ml-1">CODE_RUNNER_PROVIDER</code>, <code className="bg-gray-100 rounded px-1">CODE_RUNNER_FALLBACK</code>
      </p>
    </SectionCard>
  );
}

/** A save button plus a transient "saved" note, shared by the web cards. */
function SaveRow({ s, dirty, onSave, label, saved }) {
  const { t } = useI18n();
  return (
    <div className="flex items-center gap-3">
      <button type="button" onClick={onSave} disabled={s.webSearchSaving || !dirty}
        className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
        {s.webSearchSaving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
        {s.webSearchSaving ? t('common.saving') : label}
      </button>
      {saved && <span className="text-xs text-green-700">{t('settings.webSearchSaved')}</span>}
    </div>
  );
}

/** A short-lived "saved" flag. */
function useSavedFlag() {
  const [saved, setSaved] = useState(false);
  const flash = () => { setSaved(true); setTimeout(() => setSaved(false), 3000); };
  return [saved, flash];
}

/**
 * The search provider behind the ``web_search`` tool (tools/web.py): Brave,
 * Tavily or Exa, its key and how many results a call returns. Without a
 * provider the tool answers "not configured" and performs no search, which is
 * the message an agent then relays. Machine-wide .env settings, read live.
 */
function WebSearchProviderCard({ s }) {
  const { t } = useI18n();
  const g = s.globalSettings || {};
  // Edits are kept apart from the saved values (null = not edited), so a
  // reload of the settings shows through without an effect copying state.
  const [providerEdit, setProviderEdit] = useState(null);
  const [maxResultsEdit, setMaxResultsEdit] = useState(null);
  const [key, setKey] = useState('');
  const [saved, flash] = useSavedFlag();
  const savedProvider = g.web_search_provider || '';
  const savedMax = Number(g.web_search_max_results || 5);
  const provider = providerEdit ?? savedProvider;
  const maxResults = maxResultsEdit ?? savedMax;
  const dirty = provider !== savedProvider || key !== '' || Number(maxResults) !== savedMax;
  const configured = Boolean(g.web_search_provider) && Boolean(g.web_search_api_key_masked);
  const save = async () => {
    const patch = { web_search_provider: provider, web_search_max_results: Number(maxResults) || 5 };
    if (key) patch.web_search_api_key = key;
    const ok = await s.saveWebSearch(patch);
    if (ok) { setKey(''); setProviderEdit(null); setMaxResultsEdit(null); flash(); }
  };
  return (
    <SectionCard
      title={t('settings.webSearch')}
      actions={(
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${configured ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-800'}`}>
          {configured ? t('settings.webSearchConfigured') : t('settings.webSearchNotConfigured')}
        </span>
      )}
    >
      <p className="text-xs text-gray-500">{t('settings.webSearchIntro')}</p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{t('settings.webSearchProvider')}</label>
          <select value={provider} onChange={e => setProviderEdit(e.target.value)} className={inputCls} aria-label={t('settings.webSearchProvider')}>
            <option value="">{t('settings.webSearchProviderNone')}</option>
            <option value="brave">Brave Search</option>
            <option value="tavily">Tavily</option>
            <option value="exa">Exa</option>
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('settings.webSearchApiKey')}
            {g.web_search_api_key_masked && <span className="ml-2 font-mono text-xs text-gray-400">{g.web_search_api_key_masked}</span>}
          </label>
          <input type="password" value={key} onChange={e => setKey(e.target.value)}
            placeholder={g.web_search_api_key_masked ? t('settings.webSearchKeyKeep') : t('settings.webSearchKeyPlaceholder')}
            autoComplete="off" className={inputCls} aria-label={t('settings.webSearchApiKey')} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{t('settings.webSearchMaxResults')}</label>
          <input type="number" min={1} max={20} value={maxResults} onChange={e => setMaxResultsEdit(e.target.value)} className={inputCls} aria-label={t('settings.webSearchMaxResults')} />
        </div>
      </div>
      <SaveRow s={s} dirty={dirty} onSave={save} label={t('settings.webSearchSave')} saved={saved} />
      <p className="text-xs text-gray-400">
        {t('settings.globalSettingWrittenTo')} <code className="bg-gray-100 rounded px-1">.env</code> {t('settings.as')}
        <code className="bg-gray-100 rounded px-1 ml-1">WEB_SEARCH_PROVIDER</code>, <code className="bg-gray-100 rounded px-1">WEB_SEARCH_API_KEY</code>, <code className="bg-gray-100 rounded px-1">WEB_SEARCH_MAX_RESULTS</code>
      </p>
    </SectionCard>
  );
}

/**
 * ``fetch_url`` limits (tools/web.py): how much text a page may return by
 * default, the request timeout and how many redirect hops are followed, each
 * hop re-checked against the private-address and domain rules.
 */
function WebFetchCard({ s }) {
  const { t } = useI18n();
  const g = s.globalSettings || {};
  const savedVals = {
    web_fetch_max_chars: Number(g.web_fetch_max_chars || 20000),
    web_fetch_timeout: Number(g.web_fetch_timeout || 20),
    web_fetch_max_redirects: Number(g.web_fetch_max_redirects ?? 5),
  };
  const [edit, setEdit] = useState({});
  const [saved, flash] = useSavedFlag();
  const val = (k) => edit[k] ?? savedVals[k];
  const dirty = Object.keys(savedVals).some((k) => Number(val(k)) !== savedVals[k]);
  const save = async () => {
    const ok = await s.saveWebSearch({
      web_fetch_max_chars: Number(val('web_fetch_max_chars')),
      web_fetch_timeout: Number(val('web_fetch_timeout')),
      web_fetch_max_redirects: Number(val('web_fetch_max_redirects')),
    });
    if (ok) { setEdit({}); flash(); }
  };
  const field = (k, label, hint, min, max, step = 1) => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <input type="number" min={min} max={max} step={step} value={val(k)} onChange={e => setEdit(prev => ({ ...prev, [k]: e.target.value }))} className={inputCls} aria-label={label} />
      <p className="text-xs text-gray-500 mt-1">{hint}</p>
    </div>
  );
  return (
    <SectionCard title={t('settings.webFetch')}>
      <p className="text-xs text-gray-500">{t('settings.webFetchIntro')}</p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {field('web_fetch_max_chars', t('settings.webFetchMaxChars'), t('settings.webFetchMaxCharsHint'), 500, 200000, 500)}
        {field('web_fetch_timeout', t('settings.webFetchTimeout'), t('settings.webFetchTimeoutHint'), 1, 120)}
        {field('web_fetch_max_redirects', t('settings.webFetchMaxRedirects'), t('settings.webFetchMaxRedirectsHint'), 0, 20)}
      </div>
      <SaveRow s={s} dirty={dirty} onSave={save} label={t('settings.webFetchSave')} saved={saved} />
      <p className="text-xs text-gray-400">
        {t('settings.globalSettingWrittenTo')} <code className="bg-gray-100 rounded px-1">.env</code> {t('settings.as')}
        <code className="bg-gray-100 rounded px-1 ml-1">WEB_FETCH_MAX_CHARS</code>, <code className="bg-gray-100 rounded px-1">WEB_FETCH_TIMEOUT</code>, <code className="bg-gray-100 rounded px-1">WEB_FETCH_MAX_REDIRECTS</code>
      </p>
    </SectionCard>
  );
}

const linesOf = (list) => (list || []).join('\n');
const listOf = (text) => text.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean);

/**
 * The global domain policy for both web tools: a deny list that always
 * applies, and an opt-in allow list that, once on, is the only set of hosts
 * a fetch may reach or a search may return. A host matches itself and its
 * subdomains. A workspace may set its own lists in its settings; those
 * replace these for runs in that workspace.
 */
function WebDomainPolicyCard({ s }) {
  const { t } = useI18n();
  const g = s.globalSettings || {};
  const savedEnabled = Boolean(g.web_domain_policy_enabled);
  const savedAllow = linesOf(g.web_allow_domains);
  const savedDeny = linesOf(g.web_deny_domains);
  const [enabledEdit, setEnabledEdit] = useState(null);
  const [allowEdit, setAllowEdit] = useState(null);
  const [denyEdit, setDenyEdit] = useState(null);
  const [saved, flash] = useSavedFlag();
  const enabled = enabledEdit ?? savedEnabled;
  const allow = allowEdit ?? savedAllow;
  const deny = denyEdit ?? savedDeny;
  const dirty = enabled !== savedEnabled || allow !== savedAllow || deny !== savedDeny;
  const save = async () => {
    const ok = await s.saveWebSearch({
      web_domain_policy_enabled: enabled,
      web_allow_domains: listOf(allow),
      web_deny_domains: listOf(deny),
    });
    if (ok) { setEnabledEdit(null); setAllowEdit(null); setDenyEdit(null); flash(); }
  };
  const areaCls = `${inputCls} font-mono text-xs min-h-[6rem]`;
  return (
    <SectionCard title={t('settings.webDomains')}>
      <p className="text-xs text-gray-500">{t('settings.webDomainsIntro')}</p>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('settings.webDenyDomains')}</label>
        <textarea value={deny} onChange={e => setDenyEdit(e.target.value)} placeholder={'evil.example\ntracker.example'} className={areaCls} aria-label={t('settings.webDenyDomains')} />
        <p className="text-xs text-gray-500 mt-1">{t('settings.webDenyDomainsHint')}</p>
      </div>
      <div className="flex items-center justify-between gap-3 pt-3 border-t border-gray-100">
        <div>
          <label className="text-sm font-medium text-gray-700">{t('settings.webAllowPolicy')}</label>
          <p className="text-xs text-gray-500 mt-1">{t('settings.webAllowPolicyHint')}</p>
        </div>
        <Toggle checked={enabled} disabled={s.webSearchSaving} onChange={setEnabledEdit} />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('settings.webAllowDomains')}</label>
        <textarea value={allow} onChange={e => setAllowEdit(e.target.value)} placeholder={'wikipedia.org\narxiv.org'} className={areaCls} aria-label={t('settings.webAllowDomains')} disabled={!enabled} />
        <p className="text-xs text-gray-500 mt-1">{enabled && listOf(allow).length === 0 ? t('settings.webAllowEmptyWarning') : t('settings.webAllowDomainsHint')}</p>
      </div>
      <SaveRow s={s} dirty={dirty} onSave={save} label={t('settings.webDomainsSave')} saved={saved} />
      <p className="text-xs text-gray-400">
        {t('settings.globalSettingWrittenTo')} <code className="bg-gray-100 rounded px-1">.env</code> {t('settings.as')}
        <code className="bg-gray-100 rounded px-1 ml-1">WEB_DOMAIN_POLICY_ENABLED</code>, <code className="bg-gray-100 rounded px-1">WEB_ALLOW_DOMAINS</code>, <code className="bg-gray-100 rounded px-1">WEB_DENY_DOMAINS</code>
      </p>
    </SectionCard>
  );
}

/**
 * The same policy for one workspace (routes/workspaces.py, web-policy): its
 * lists replace the global ones for runs in that workspace, an empty list
 * falls back to the global one, and the switch turns the allow list on here
 * even when it is off globally. Loaded and saved on its own, since it lives
 * in the workspace's metadata rather than in .env.
 */
export function WorkspaceDomainPolicyCard({ workspace }) {
  const { t } = useI18n();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, flash] = useSavedFlag();
  const [enabledEdit, setEnabledEdit] = useState(null);
  const [allowEdit, setAllowEdit] = useState(null);
  const [denyEdit, setDenyEdit] = useState(null);
  const tRef = useRef(t);
  tRef.current = t;

  const load = useCallback(async () => {
    setError('');
    try {
      const { data: body } = await getWorkspaceWebPolicy(workspace);
      setData(body);
    } catch (e) {
      setError(e.response?.data?.detail || tRef.current('settings.errors.webSearchSave'));
    }
  }, [workspace]);
  useEffect(() => { load(); }, [load]);

  const policy = data?.policy || { enabled: false, allow_domains: [], deny_domains: [] };
  const global = data?.global || { enabled: false, allow_domains: [], deny_domains: [] };
  const savedAllow = linesOf(policy.allow_domains);
  const savedDeny = linesOf(policy.deny_domains);
  const enabled = enabledEdit ?? Boolean(policy.enabled);
  const allow = allowEdit ?? savedAllow;
  const deny = denyEdit ?? savedDeny;
  const dirty = enabled !== Boolean(policy.enabled) || allow !== savedAllow || deny !== savedDeny;
  const effectiveEnabled = enabled || Boolean(global.enabled);

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const { data: body } = await updateWorkspaceWebPolicy(workspace, {
        enabled, allow_domains: listOf(allow), deny_domains: listOf(deny),
      });
      setData(body);
      setEnabledEdit(null); setAllowEdit(null); setDenyEdit(null);
      flash();
    } catch (e) {
      setError(e.response?.data?.detail || t('settings.errors.webSearchSave'));
    } finally {
      setSaving(false);
    }
  };
  const areaCls = `${inputCls} font-mono text-xs min-h-[6rem]`;
  const globalHint = (list) => (list.length ? t('settings.wsDomainsGlobalList', { hosts: list.join(', ') }) : t('settings.wsDomainsGlobalEmpty'));
  return (
    <SectionCard title={t('settings.wsDomains', { workspace })}>
      <p className="text-xs text-gray-500">{t('settings.wsDomainsIntro')}</p>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('settings.webDenyDomains')}</label>
        <textarea value={deny} onChange={e => setDenyEdit(e.target.value)} className={areaCls} aria-label={`${t('settings.webDenyDomains')} (${workspace})`} />
        <p className="text-xs text-gray-500 mt-1">{listOf(deny).length ? t('settings.wsDomainsReplaces') : globalHint(global.deny_domains)}</p>
      </div>
      <div className="flex items-center justify-between gap-3 pt-3 border-t border-gray-100">
        <div>
          <label className="text-sm font-medium text-gray-700">{t('settings.webAllowPolicy')}</label>
          <p className="text-xs text-gray-500 mt-1">
            {global.enabled ? t('settings.wsAllowPolicyGlobalOn') : t('settings.webAllowPolicyHint')}
          </p>
        </div>
        <Toggle checked={enabled} disabled={saving} onChange={setEnabledEdit} />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('settings.webAllowDomains')}</label>
        <textarea value={allow} onChange={e => setAllowEdit(e.target.value)} className={areaCls} aria-label={`${t('settings.webAllowDomains')} (${workspace})`} disabled={!effectiveEnabled} />
        <p className="text-xs text-gray-500 mt-1">
          {effectiveEnabled && listOf(allow).length === 0 && global.allow_domains.length === 0
            ? t('settings.webAllowEmptyWarning')
            : listOf(allow).length ? t('settings.wsDomainsReplaces') : globalHint(global.allow_domains)}
        </p>
      </div>
      <div className="flex items-center gap-3">
        <button type="button" onClick={save} disabled={saving || !dirty || !data}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? t('common.saving') : t('settings.wsDomainsSave', { workspace })}
        </button>
        {saved && <span className="text-xs text-green-700">{t('settings.webSearchSaved')}</span>}
      </div>
      {error && <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>}
    </SectionCard>
  );
}

/** The Web search page of Settings: provider, fetch limits and the global
 * domain policy. A workspace's own policy is on the workspace page, Settings
 * tab, "Web access" (WorkspaceDomainPolicyCard). */
export function WebSearchSection({ s }) {
  return (
    <div className="space-y-5">
      <WebSearchProviderCard s={s} />
      <WebFetchCard s={s} />
      <WebDomainPolicyCard s={s} />
    </div>
  );
}

/**
 * Agent execution on the Settings page: the three sections above. `showGlobal`
 * adds the token streaming switch, which is a machine-wide .env setting rather
 * than a workspace one, so only the Settings page shows it.
 */
export function ExecutionSection({ s, showGlobal = false }) {
  const { t } = useI18n();
  return (
    <div className="space-y-5">
      {showGlobal && (
        <SectionCard title={t('settings.liveStreaming')}>
          <div className="flex items-center justify-between gap-3">
            <div>
              <label className="text-sm font-medium text-gray-700">{t('settings.streamAgentOutput')}</label>
              <p className="text-xs text-gray-500 mt-1">
                {t('settings.streamingHintBefore')} <strong>{t('settings.stop')}</strong>{t('settings.streamingHintAfter')}
              </p>
              <p className="text-xs text-gray-400 mt-1">
                {t('settings.globalSettingWrittenTo')} <code className="bg-gray-100 rounded px-1">.env</code> {t('settings.as')}
                <code className="bg-gray-100 rounded px-1 ml-1">AGENT_STREAMING</code>{t('settings.appliesToEvery')}{' '}
                {t('settings.streamingAgentOverrideBefore')} <code className="bg-gray-100 rounded px-1">{t('settings.streaming')}</code> {t('settings.streamingAgentOverrideAfter')}
              </p>
            </div>
            <Toggle checked={!!s.globalSettings.agent_streaming} disabled={s.streamingSaving} onChange={s.toggleStreaming} />
          </div>
        </SectionCard>
      )}

      <ExecutionModeSection s={s} />
      {showGlobal && <CapabilityGuardSection s={s} />}
      {showGlobal && <CodeRunnerSection s={s} />}
      <ToolPolicySection s={s} />
      <TaskAssignmentSection s={s} />
    </div>
  );
}

/** The Save button for the override fields, labelled with the workspace it writes to. */
export function SaveWorkspaceSettingsButton({ s }) {
  const { t } = useI18n();
  return (
    <button onClick={s.save} disabled={s.saving || s.loading}
      className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
      {s.saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
      {s.saving ? t('common.saving') : t('settings.saveWorkspaceSettings', { workspace: s.workspace })}
    </button>
  );
}

/** The saved and error banners that go above the sections. */
export function WorkspaceSettingsStatus({ s }) {
  const { t } = useI18n();
  return (
    <>
      {s.saved && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
          {t('settings.workspaceSettingsSaved', { workspace: s.workspace })}
        </div>
      )}
      {s.error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{s.error}</div>
      )}
    </>
  );
}
