import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CheckCircle2,
  Circle,
  Loader2,
  RefreshCw,
  ArrowRight,
  AlertTriangle,
} from 'lucide-react';
import { getSystemHealth, getSettings, getWorkspaces, getAgents, testProvider } from '../../api';
import { useI18n } from '../../i18n';

// ---------------------------------------------------------------------------
// OnboardingChecklist — a *live* first-steps checklist.
//
// Unlike a static tutorial, every step inspects the real backend state
// (health, configured providers, workspaces, agents) and renders a green tick
// once satisfied. Each step deep-links into the page where the user completes
// it. Used both inside the first-run modal and on the Docs "Getting Started"
// page, so it accepts an `onNavigate` callback (the modal uses it to close
// itself before routing).
// ---------------------------------------------------------------------------

const STATUS = { LOADING: 'loading', DONE: 'done', TODO: 'todo', WARN: 'warn' };

function StatusIcon({ status }) {
  if (status === STATUS.LOADING) return <Loader2 className="w-5 h-5 text-gray-400 animate-spin shrink-0" />;
  if (status === STATUS.DONE) return <CheckCircle2 className="w-5 h-5 text-green-500 shrink-0" />;
  if (status === STATUS.WARN) return <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0" />;
  return <Circle className="w-5 h-5 text-gray-300 shrink-0" />;
}

export default function OnboardingChecklist({ onNavigate }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [state, setState] = useState({
    backend: STATUS.LOADING,
    provider: STATUS.LOADING,
    workspace: STATUS.LOADING,
    agent: STATUS.LOADING,
    providerLabel: '',
    workspaceCount: 0,
    agentCount: 0,
  });

  // Note: no synchronous setState at the top — `loading` already starts true on
  // mount, and the manual re-check button flips it before calling this. That
  // keeps the mount effect free of synchronous state updates.
  const refresh = useCallback(async () => {
    // Backend health. A failure here is the one step the user cannot act on
    // from inside the app, so it is reported as a warning rather than an
    // unticked box — otherwise a backend that is simply down looks identical
    // to a step not started yet, and the only evidence is a console error.
    let backend = STATUS.TODO;
    try {
      await getSystemHealth();
      backend = STATUS.DONE;
    } catch {
      backend = STATUS.WARN;
    }

    // Provider configured? A cloud key set (masked != "****") or a local model.
    let provider = STATUS.TODO;
    let providerLabel = '';
    if (backend === STATUS.DONE) {
      try {
        const { data } = await getSettings();
        const hasCloudKey = [data.openai_api_key_masked, data.anthropic_api_key_masked, data.google_api_key_masked]
          .some((m) => m && m !== '****');
        const hasLocal = Boolean(data.ollama_model || data.lmstudio_model);
        provider = hasCloudKey || hasLocal ? STATUS.DONE : STATUS.TODO;
        providerLabel = data.default_provider || '';
      } catch {
        provider = STATUS.TODO;
      }
    }

    // Workspace + agent presence
    let workspace = STATUS.TODO;
    let workspaceCount = 0;
    let agent = STATUS.TODO;
    let agentCount = 0;
    if (backend === STATUS.DONE) {
      try {
        const { data } = await getWorkspaces();
        workspaceCount = (data || []).length;
        workspace = workspaceCount > 0 ? STATUS.DONE : STATUS.TODO;
      } catch { /* leave TODO */ }
      try {
        const { data } = await getAgents();
        agentCount = (data || []).length;
        agent = agentCount > 0 ? STATUS.DONE : STATUS.WARN;
      } catch { /* leave TODO */ }
    }

    setState({ backend, provider, workspace, agent, providerLabel, workspaceCount, agentCount });
    setLoading(false);
  }, []);

  useEffect(() => {
    (async () => { await refresh(); })();
  }, [refresh]);

  const handleRecheck = () => {
    setLoading(true);
    refresh();
  };

  const go = (path) => {
    if (onNavigate) onNavigate();
    navigate(path);
  };

  const testDefaultProvider = async () => {
    if (!state.providerLabel) return;
    setState((s) => ({ ...s, provider: STATUS.LOADING }));
    try {
      const { data } = await testProvider({ provider: state.providerLabel });
      setState((s) => ({ ...s, provider: data?.ok ? STATUS.DONE : STATUS.WARN }));
    } catch {
      setState((s) => ({ ...s, provider: STATUS.WARN }));
    }
  };

  const steps = [
    {
      key: 'backend',
      status: state.backend,
      title: t('onboardingChecklist.backend.title'),
      desc:
        state.backend === STATUS.DONE
          ? t('onboardingChecklist.backend.done')
          : state.backend === STATUS.WARN
          ? t('onboardingChecklist.backend.unreachable')
          : t('onboardingChecklist.backend.todo'),
      action: null,
    },
    {
      key: 'provider',
      status: state.provider,
      title: t('onboardingChecklist.provider.title'),
      desc:
        state.provider === STATUS.DONE
          ? (state.providerLabel
            ? t('onboardingChecklist.provider.doneWithLabel', { provider: state.providerLabel })
            : t('onboardingChecklist.provider.done'))
          : state.provider === STATUS.WARN
          ? t('onboardingChecklist.provider.warn')
          : t('onboardingChecklist.provider.todo'),
      action: { label: t('onboardingChecklist.provider.openSettings'), onClick: () => go('/settings/providers') },
      secondary:
        state.providerLabel && state.provider !== STATUS.LOADING
          ? { label: t('onboardingChecklist.provider.testConnection'), onClick: testDefaultProvider }
          : null,
    },
    {
      key: 'workspace',
      status: state.workspace,
      title: t('onboardingChecklist.workspace.title'),
      desc:
        state.workspace === STATUS.DONE
          ? t('onboardingChecklist.workspace.done', { count: state.workspaceCount })
          : t('onboardingChecklist.workspace.todo'),
      action: { label: t('onboardingChecklist.workspace.manage'), onClick: () => go('/workspaces') },
    },
    {
      key: 'agent',
      status: state.agent,
      title: t('onboardingChecklist.agent.title'),
      desc:
        state.agent === STATUS.DONE
          ? t('onboardingChecklist.agent.done', { count: state.agentCount })
          : t('onboardingChecklist.agent.todo'),
      action: { label: t('onboardingChecklist.agent.openMarketplace'), onClick: () => go('/marketplace') },
    },
    {
      key: 'chat',
      status: STATUS.TODO,
      title: t('onboardingChecklist.chat.title'),
      desc: t('onboardingChecklist.chat.desc'),
      action: { label: t('onboardingChecklist.chat.openChat'), onClick: () => go('/chat'), primary: true },
    },
  ];

  const doneCount = steps.filter((s) => s.status === STATUS.DONE).length;
  const total = steps.length - 1; // the final "start chat" step isn't auto-detectable

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-700">
            {t('onboardingChecklist.setupProgress', { done: Math.min(doneCount, total), total })}
          </span>
          <div className="w-32 h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-500 transition-all"
              style={{ width: `${(Math.min(doneCount, total) / total) * 100}%` }}
            />
          </div>
        </div>
        <button
          onClick={handleRecheck}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-indigo-600 disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          {t('onboardingChecklist.reCheck')}
        </button>
      </div>

      <ol className="space-y-2">
        {steps.map((step) => (
          <li
            key={step.key}
            className="flex items-start gap-3 p-3 rounded-xl border border-gray-200 bg-white"
          >
            <StatusIcon status={step.status} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-gray-900">{step.title}</p>
              <p className="text-xs text-gray-500 mt-0.5">{step.desc}</p>
            </div>
            <div className="flex flex-col items-end gap-1 shrink-0">
              {step.action && (
                <button
                  onClick={step.action.onClick}
                  className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${
                    step.action.primary
                      ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                      : 'text-indigo-600 hover:bg-indigo-50'
                  }`}
                >
                  {step.action.label}
                  <ArrowRight className="w-3 h-3" />
                </button>
              )}
              {step.secondary && (
                <button
                  onClick={step.secondary.onClick}
                  className="text-[11px] font-medium text-gray-400 hover:text-gray-600"
                >
                  {step.secondary.label}
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
