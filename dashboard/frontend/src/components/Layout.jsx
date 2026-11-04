import { useState, useEffect, useCallback, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useWorkspace } from './workspace';
import { useStream, useLiveRefetch } from './stream';
import { useFeatures } from './features';
import { MULTI, canAdminister, useAuth } from './auth';
import { buildMenu, visibleGroups, hiddenCount, defaultMenuMode, readMenuMode, writeMenuMode, isItemActive } from './navMenu';
import { getWorkspaces, updateWorkspaceModel, testProvider, getModelsCatalog, getModelReasoning } from '../api';
import { EffortSlider } from './EffortSelect';
import { clampEffort, effortLabel } from './effort';
import { policyAllows, policyModels } from './workspace/modelPolicy';
import {
  loadWorkspaceSummary, patchWorkspaceSummary, subscribeWorkspaceSummary, invalidateAllWorkspaceSummaries,
} from '../api/workspaceSummary';
import { Cpu, ChevronDown, PanelLeftClose, PanelLeftOpen, Menu, X, ListChecks } from 'lucide-react';
import NotificationBell from './NotificationBell';
import WatchersIndicator from './WatchersIndicator';
import SyncIndicator from './SyncIndicator';
import SyncedDataBanner from './vault/SyncedDataBanner';
import IsolationBadge from './workspace/IsolationBadge';
import { useI18n } from '../i18n';
import OnboardingModal from './docs/OnboardingModal';
import useSetupGuide from './setup/useSetupGuide';
import { routeTitleKey } from './routeTitles';
import PageChatPanel from './pageChat/PageChatPanel';
import { usePageChatPanel } from './pageChat/pageChat';
import HelpPanel from './help/HelpPanel';
import { isEmbedded } from './embed';
import WakeListener from './assistant/WakeListener';
import WorkspacePicker from './WorkspacePicker';
import NoWorkspaceScreen from './NoWorkspaceScreen';
import AccountMenu from './AccountMenu';
import { PHONE_QUERY } from '../lib/phone';
import { PhoneTitleSlot } from './phoneTitleSlot';

const SIDEBAR_COLLAPSED_KEY = 'agents_hub_sidebar_collapsed';
// The project mark, also the browser tab icon (index.html); served from public/.
const LOGO_URL = `${import.meta.env.BASE_URL}logo.svg`;

const PROVIDER_CONFIG = {
  openai:    { label: 'OpenAI',    color: 'text-green-700 bg-green-50 border-green-200' },
  anthropic: { label: 'Anthropic', color: 'text-orange-700 bg-orange-50 border-orange-200' },
  google:    { label: 'Google',    color: 'text-blue-700 bg-blue-50 border-blue-200' },
  ollama:    { label: 'Ollama',    color: 'text-purple-700 bg-purple-50 border-purple-200' },
  lmstudio:  { label: 'LM Studio', color: 'text-teal-700 bg-teal-50 border-teal-200' },
};

const BUILTIN_PROVIDER_ORDER = ['openai', 'anthropic', 'google', 'ollama', 'lmstudio'];

// A phone held either way: the menu is a drawer, closed until asked for.
const NARROW_QUERY = PHONE_QUERY;

// How often the chosen model's provider is tested again for the chip's colour.
const PROVIDER_RETEST_MS = 5 * 60 * 1000;

function useNarrow() {
  const [narrow, setNarrow] = useState(() => (typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia(NARROW_QUERY).matches : false));
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined;
    const mq = window.matchMedia(NARROW_QUERY);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  return narrow;
}

const Layout = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { selectedWorkspace, setSelectedWorkspace, liveUpdates, toggleLiveUpdates } = useWorkspace();
  // Optional features the backend reports at /api/health; a feature that is
  // switched off has no sidebar row and no route (see App.jsx).
  const features = useFeatures();
  const auth = useAuth();
  const { t } = useI18n();
  // The guided setup's own pill, while it runs (docs/assistant.md "Guided
  // setup"); cheap, since useSetupGuide only polls while the guide is
  // active or a step's own work is going on.
  const { guide, act: guideAct } = useSetupGuide();
  const [workspaces, setWorkspaces] = useState([]);
  // workspaceModel: the `model` part of GET /api/workspaces/{name}/summary
  // - global_default: DEFAULT_PROVIDER + its model from .env (lowest-priority fallback)
  // - workspace_default: resolved from workspace settings in .workspace.json
  // - override: explicitly set via UI picker
  const [workspaceModel, setWorkspaceModel] = useState({
    global_default: { provider: 'openai', model: '' },
    workspace_default: { provider: '', model: '' },
    override: { provider: '', model: '' },
  });
  // availableModels: all providers with a model configured in Settings, used for override rows
  const [availableModels, setAvailableModels] = useState([]);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [providerStatuses, setProviderStatuses] = useState({});
  const [providersTesting, setProvidersTesting] = useState({});
  const [sidebarPref, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
    } catch {
      return false;
    }
  });
  // On a phone the menu is a drawer over the page, closed until the menu
  // button opens it and closed again by any navigation. Even folded to icons
  // it would cost a sixth of the screen there.
  const narrow = useNarrow();
  // The phone top bar's title slot (PageLayout.jsx, PhoneTitleSlot).
  const [phoneTitleSlot, setPhoneTitleSlot] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const sidebarCollapsed = narrow ? false : sidebarPref;
  const modelPickerRef = useRef(null);
  // Any navigation closes the drawer: adjust state while rendering rather
  // than in an effect, so the stale open frame is never painted.
  const [drawerPath, setDrawerPath] = useState(location.pathname);
  if (drawerPath !== location.pathname) {
    setDrawerPath(location.pathname);
    setDrawerOpen(false);
  }

  const toggleSidebar = () => {
    if (narrow) { setDrawerOpen((v) => !v); return; }
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? '1' : '0');
      } catch { /* storage unavailable */ }
      return next;
    });
  };

  // The list is fetched once (and again on a live change below), not on
  // every switch: picking a workspace does not change what exists.
  const [workspacesLoaded, setWorkspacesLoaded] = useState(false);
  useEffect(() => {
    getWorkspaces()
      .then((resp) => { setWorkspaces(resp.data); setWorkspacesLoaded(true); })
      .catch((error) => console.error('Error fetching workspaces:', error));
  }, []);

  // Fall back to the first workspace when none is picked yet or the picked
  // one is gone.
  useEffect(() => {
    if (!workspacesLoaded || workspaces.length === 0) return;
    const stale = selectedWorkspace && !workspaces.some(w => w.name === selectedWorkspace);
    if (!selectedWorkspace || stale) setSelectedWorkspace(workspaces[0].name);
  }, [workspacesLoaded, workspaces, selectedWorkspace, setSelectedWorkspace]);

  // Keep the picker in sync when workspaces are created/deleted elsewhere.
  useLiveRefetch(() => {
    getWorkspaces()
      .then((resp) => setWorkspaces(resp.data))
      .catch((error) => console.error('Error refreshing workspaces:', error));
  }, { type: 'workspaces.changed' });

  // Fetch every enabled catalog model (multiple per provider) — these are the
  // selectable rows in the picker. Re-fetched when the picker opens so newly
  // enabled/disabled models in the Models page show up without a full reload.
  // Built-in providers first, then any custom backends present in the catalog,
  // so user-defined backends' enabled models also appear in the picker.
  const loadAvailableModels = useCallback(() => {
    return getModelsCatalog().then(({ data }) => {
      const providers = data.providers || {};
      const order = [
        ...BUILTIN_PROVIDER_ORDER.filter((p) => p in providers),
        ...Object.keys(providers).filter((p) => !BUILTIN_PROVIDER_ORDER.includes(p)).sort(),
      ];
      const avail = [];
      for (const p of order) {
        for (const m of (providers[p]?.models || [])) {
          if (m.enabled) avail.push({ provider: p, model: m.id });
        }
      }
      setAvailableModels(avail);
      return avail;
    }).catch(e => { console.error('Error fetching catalog:', e); return []; });
  }, []);
  useEffect(() => { loadAvailableModels(); }, [loadAvailableModels]);

  // Re-fetch workspace model whenever workspace changes
  useEffect(() => {
    if (!selectedWorkspace) return;
    loadWorkspaceSummary(selectedWorkspace)
      .then((summary) => { if (summary?.model) setWorkspaceModel(summary.model); })
      .catch(() => setWorkspaceModel(prev => ({ ...prev, override: { provider: '', model: '' }, workspace_default: { provider: '', model: '' } })));
  }, [selectedWorkspace]);

  // Any later answer or patch for this workspace (the Models page saving the
  // global or the workspace default) reaches the picker at once.
  useEffect(() => {
    if (!selectedWorkspace) return undefined;
    return subscribeWorkspaceSummary(selectedWorkspace, (summary) => {
      if (summary?.model) setWorkspaceModel(summary.model);
    });
  }, [selectedWorkspace]);

  // A model choice saved elsewhere (another tab, another administrator, the
  // catalog) announces itself as models.changed: read the summary and the
  // enabled models again. The global default is in every workspace's summary.
  useLiveRefetch(() => {
    invalidateAllWorkspaceSummaries();
    if (selectedWorkspace) loadWorkspaceSummary(selectedWorkspace, { force: true }).catch(() => {});
    loadAvailableModels();
  }, { type: 'models.changed' });

  // Backend health is implied by the shared stream connection: if our single
  // EventSource is open, the backend is up. No separate polling needed.
  const { connected } = useStream();
  const backendOnline = connected;

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target)) {
        setShowModelPicker(false);
      }
    };
    if (showModelPicker) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showModelPicker]);

  const SERVICE_NAME = t('layout.serviceName');

  useEffect(() => {
    const key = routeTitleKey(location.pathname);
    const pageTitle = key ? t(key) : '';
    document.title = pageTitle ? `${SERVICE_NAME} - ${pageTitle}` : SERVICE_NAME;
  }, [location.pathname, t, SERVICE_NAME]);

  // Resolve effective display model using three-tier priority:
  // 1. explicit override (set via picker), 2. workspace default model from settings, 3. global DEFAULT_PROVIDER
  const globalDefault = workspaceModel.global_default || { provider: 'openai', model: '' };
  // A workspace default that resolves to the very same provider+model as the
  // global default is not a distinct choice — treat it as "global" so the picker
  // doesn't show the same provider twice (once as Global, once as Default).
  const wsDefault = workspaceModel.workspace_default || { provider: '', model: '' };
  // The administrator's model policy of the workspace (workspace/model_policy.py):
  // the picker offers its models only, and its default is the workspace default.
  const policy = workspaceModel.policy?.models?.length ? workspaceModel.policy : null;
  const pickerModels = policy ? policyModels(policy) : availableModels;
  // A member (multi mode, not an administrator) changes the model for the
  // whole workspace from here, so only within what the hub offers (the
  // listed models), and the reasoning effort not at all (PUT
  // /api/workspaces/{name}/model refuses the same).
  const member = !canAdminister(auth);
  const workspaceDefaultIsGlobal = !policy && Boolean(
    wsDefault.provider &&
    wsDefault.provider === globalDefault.provider &&
    (wsDefault.model || '') === (globalDefault.model || '')
  );
  // A personal workspace (common/personal_workspace.py) reads as the
  // person's own, never by its generated name.
  const workspaceLabel = (ws) => {
    if (ws.name === 'default') return t('layout.workspaceDefaultAll');
    if (ws.own_personal) return t('layout.workspacePersonal');
    if (ws.personal) return t('layout.workspacePersonalOf', { name: ws.personal_label || ws.personal_of });
    return ws.name;
  };

  // The model the workspace runs without a choice in the menu's model picker: its own
  // default (the policy's under a policy), else the global one.
  const inheritedModel = (() => {
    const dp = workspaceModel.workspace_default?.provider || '';
    if (dp && (dp !== 'global' || policy) && !workspaceDefaultIsGlobal) {
      return { provider: dp, model: workspaceModel.workspace_default.model, source: 'workspace_default' };
    }
    return { ...globalDefault, source: 'global' };
  })();

  const displayModel = (() => {
    const op = workspaceModel.override?.provider || '';
    const overrideAllowed = !policy
      || (op !== 'global' && policyAllows(policy, op, workspaceModel.override?.model));
    if (op && op !== 'workspace_default' && overrideAllowed) {
      if (op === 'global') return { ...globalDefault, source: 'global' };
      return { provider: op, model: workspaceModel.override.model, source: 'override' };
    }
    return inheritedModel;
  })();

  // The menu's reasoning effort, for agents that inherit the model: kept
  // apart from the model, so it outlives a switch, and shown as the displayed
  // model can take it (providers/reasoning_profile.py).
  const workspaceEffort = workspaceModel.effort || null;
  const [displayProfile, setDisplayProfile] = useState(null);
  const displayProvider = displayModel.provider;
  const displayModelId = displayModel.model;
  useEffect(() => {
    let live = true;
    getModelReasoning({ provider: displayProvider || '', model: displayModelId || '' })
      .then(({ data }) => { if (live) setDisplayProfile(data); })
      .catch(() => { if (live) setDisplayProfile(null); });
    return () => { live = false; };
  }, [displayProvider, displayModelId]);
  const effortShown = workspaceEffort
    ? clampEffort(displayProfile?.profile, workspaceEffort) || workspaceEffort
    : null;

  const handleEffortSwitch = async (effort) => {
    try {
      await updateWorkspaceModel(selectedWorkspace || 'default', { effort: effort || '' });
      const next = { ...workspaceModel, effort: effort || null, effort_own: effort || null };
      setWorkspaceModel(next);
      patchWorkspaceSummary(selectedWorkspace || 'default', { model: next });
    } catch (e) {
      console.error('Error switching workspace effort:', e);
    }
  };

  const handleModelSwitch = async (provider, model) => {
    try {
      await updateWorkspaceModel(selectedWorkspace || 'default', { provider, model });
      // Update override state; workspace_default is read-only from picker perspective
      const newOverride = (!provider || provider === 'workspace_default')
        ? { provider: '', model: '' }
        : { provider, model };
      const next = { ...workspaceModel, override: newOverride };
      setWorkspaceModel(next);
      patchWorkspaceSummary(selectedWorkspace || 'default', { model: next });
      setShowModelPicker(false);
    } catch (e) {
      console.error('Error switching workspace model:', e);
    }
  };

  // Test each provider once (deduped) — the dot is per-provider, but multiple
  // models per provider now share it.
  const runProviderTests = useCallback((providers) => {
    [...new Set(providers)].filter(Boolean).forEach((provider) => {
      setProvidersTesting(s => ({ ...s, [provider]: true }));
      testProvider({ provider })
        .then(({ data }) => setProviderStatuses(s => ({ ...s, [provider]: data })))
        .catch(e => setProviderStatuses(s => ({ ...s, [provider]: { ok: false, reason: 'failed', error: e.message } })))
        .finally(() => setProvidersTesting(s => ({ ...s, [provider]: false })));
    });
  }, []);

  // The chosen model's own status is on its chip, so its provider is tested
  // as soon as it is known and every few minutes after, not only when the
  // picker opens.
  useEffect(() => {
    if (!displayProvider) return undefined;
    runProviderTests([displayProvider]);
    const timer = setInterval(() => runProviderTests([displayProvider]), PROVIDER_RETEST_MS);
    return () => clearInterval(timer);
  }, [displayProvider, runProviderTests]);

  // ok, down (the service does not answer or is not set up: red) or failed
  // (reached or tried, but the connection did not go through: yellow); null
  // while untested. `reason` comes from POST /api/settings/test-provider.
  const providerHealth = (provider) => {
    const s = providerStatuses[provider];
    if (!s) return null;
    if (s.ok) return 'ok';
    return s.reason === 'unreachable' || s.reason === 'not_configured' ? 'down' : 'failed';
  };
  const chipHealth = providerHealth(displayModel.provider);
  const chipError = chipHealth === 'down' || chipHealth === 'failed' ? providerStatuses[displayModel.provider]?.error : '';

  const openModelPicker = () => {
    const opening = !showModelPicker;
    setShowModelPicker(opening);
    if (opening) {
      // Pick up enable/disable changes made on the Models page, then test only
      // the providers the picker actually shows a dot for: the global default
      // plus whatever providers still have enabled models.
      loadAvailableModels().then((avail) => {
        runProviderTests([globalDefault.provider, ...avail.map((a) => a.provider)]);
      });
    }
  };

  const statusDot = (provider) => {
    if (providersTesting[provider]) return <span className="w-2 h-2 rounded-full bg-gray-300 animate-pulse shrink-0" />;
    const health = providerHealth(provider);
    if (!health) return <span className="w-2 h-2 rounded-full bg-gray-300 shrink-0" />;
    const color = { ok: 'bg-green-500', down: 'bg-red-500', failed: 'bg-amber-400' }[health];
    return <span className={`w-2 h-2 rounded-full shrink-0 ${color}`} />;
  };

  // The map itself lives in navMenu.js: five groups, and in the simple menu
  // only the rows a newcomer needs. The viewer's own choice wins over the
  // default for their role, and is kept per browser.
  const [menuChoice, setMenuChoice] = useState(readMenuMode);
  const menuMode = menuChoice || defaultMenuMode(auth);
  const allGroups = buildMenu({ t, auth, features });
  const menuGroups = visibleGroups(allGroups, menuMode);
  const moreCount = hiddenCount(allGroups);
  // Chosen in Personalization, in the account menu.
  const changeMenuMode = (next) => {
    writeMenuMode(next);
    setMenuChoice(next);
  };

  const handleWorkspaceChange = (newWs) => {
    setSelectedWorkspace(newWs);
    if (location.pathname.startsWith('/workspaces/')) {
      navigate('/workspaces');
    }
  };

  // Help opens from the account menu; like its own button did, it takes the
  // place of the page chat panel.
  const pageChat = usePageChatPanel();
  const [helpOpen, setHelpOpen] = useState(false);
  const openHelp = () => {
    pageChat.setOpen(false);
    setHelpOpen(true);
  };

  // The live status beside the account name: the shared stream is the
  // backend's health, the pause is the person's own.
  const liveStatus = !backendOnline ? 'offline' : liveUpdates ? 'online' : 'paused';

  // Panels opened from the foot of the sidebar (notifications, watchers,
  // sync): beside the menu, bottom-aligned with the block, on a wide screen;
  // above the block across the drawer on a phone. Placed against the block,
  // which is `relative`, so the wrappers of the buttons are not.
  const footPanel = (width) => (narrow
    ? 'absolute left-2 right-2 bottom-full mb-2 max-h-[70vh] overflow-y-auto'
    : `absolute left-full bottom-0 ml-3 ${width}`);

  // A page the assistant opened beside itself ("show on screen"): the page
  // alone, without the sidebar and the panels around it.
  // A person who reaches no workspace at all (their personal one was deleted
  // and they belong to no other): nothing works without one, so the screen
  // says so and offers to make the personal workspace again.
  if (auth.mode === MULTI && auth.user && workspacesLoaded && workspaces.length === 0) {
    return (
      <NoWorkspaceScreen
        onCreated={(name) => getWorkspaces().then((resp) => {
          setWorkspaces(resp.data);
          if (name) setSelectedWorkspace(name);
        })}
      />
    );
  }

  if (isEmbedded()) {
    return <main className="app-shell h-viewport overflow-y-auto">{children}</main>;
  }

  return (
    <PhoneTitleSlot.Provider value={narrow ? phoneTitleSlot : null}>
    <div className="app-shell flex h-viewport overflow-hidden px-safe">
      {/* First-run onboarding (auto-opens once; re-openable from Docs) */}
      <OnboardingModal />
      {narrow && drawerOpen && (
        <div className="fixed inset-0 z-40 bg-black/40" onClick={() => setDrawerOpen(false)} aria-hidden="true" />
      )}
      {/* Sidebar: a column beside the page, or on a phone a drawer over it */}
      <div
        inert={narrow && !drawerOpen}
        className={`${
          narrow
            ? `fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] pt-safe pb-safe transition-transform duration-200 ${drawerOpen ? 'translate-x-0' : '-translate-x-full'}`
            : `${sidebarCollapsed ? 'w-16' : 'w-64'} h-full relative z-40 transition-[width] duration-200`
        } bg-white shadow-md border-r border-gray-200 flex flex-col shrink-0`}
      >
        {/* The column itself does not scroll, only the menu in it does, so the
            panels opened from the workspace switch and from the account block
            hang over the page instead of being cut off at the column's edge. */}
        {/* The mark and the name, and the fold button on their right (on a
            phone, the button that closes the drawer); folded, the mark with
            the button under it. */}
        <div className={`flex items-center gap-2 pt-4 pb-3 shrink-0 ${sidebarCollapsed ? 'flex-col px-2' : 'pl-6 pr-3'}`}>
          <img src={LOGO_URL} alt={sidebarCollapsed ? t('layout.serviceName') : ''} className={`${sidebarCollapsed ? 'w-9 h-9' : 'w-8 h-8'} shrink-0`} />
          {!sidebarCollapsed && (
            <h1 className="text-2xl font-bold leading-none text-indigo-600 truncate min-w-0">{t('layout.serviceName')}</h1>
          )}
          <button
            type="button"
            onClick={narrow ? () => setDrawerOpen(false) : toggleSidebar}
            title={narrow ? t('layout.closeMenu') : sidebarCollapsed ? t('layout.expandSidebar') : t('layout.collapseSidebar')}
            aria-label={narrow ? t('layout.closeMenu') : sidebarCollapsed ? t('layout.expandSidebar') : t('layout.collapseSidebar')}
            className={`${sidebarCollapsed ? '' : 'ml-auto'} p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 hover:text-indigo-600 transition-colors shrink-0`}
          >
            {narrow ? <X className="w-5 h-5" /> : sidebarCollapsed ? <PanelLeftOpen className="w-5 h-5" /> : <PanelLeftClose className="w-5 h-5" />}
          </button>
        </div>
        {/* The workspace switch, right under the mark: everything below works
            in the workspace picked here. Folded, a folder icon opens the same
            list beside the menu. */}
        <div className={`shrink-0 pb-2 ${sidebarCollapsed ? 'px-2 flex justify-center' : 'px-4'}`}>
          <WorkspacePicker
            workspaces={workspaces}
            value={selectedWorkspace}
            onChange={handleWorkspaceChange}
            label={workspaceLabel}
            variant={sidebarCollapsed ? 'icon' : 'block'}
            panelClassName={narrow
              ? 'absolute left-0 right-0 top-full mt-1.5'
              : sidebarCollapsed ? 'absolute left-full top-0 ml-3 w-72' : 'absolute left-0 top-full mt-1.5 w-72'}
          />
          {!sidebarCollapsed && (
            <div className="mt-1.5 px-1 empty:hidden"><IsolationBadge workspace={selectedWorkspace} /></div>
          )}
        </div>
        {/* The model the workspace runs, under the workspace switch and fixed
            there like it: the menu below scrolls, these do not. */}
        <div className={`shrink-0 pb-2 ${sidebarCollapsed ? 'px-2 flex justify-center' : 'px-4'}`}>
          {/* Global model picker; folded, the chip is its icon alone */}
          <div className={`relative ${sidebarCollapsed ? '' : 'w-full'}`} ref={modelPickerRef}>
            <button
              onClick={openModelPicker}
              title={`${PROVIDER_CONFIG[displayModel.provider]?.label || displayModel.provider}${displayModel.model ? ` · ${displayModel.model}` : ''}${effortShown ? ` · ${effortLabel(t, effortShown)}` : ''}${chipError ? `\n${chipError}` : ''}`}
              data-health={chipHealth || 'untested'}
              className={`flex items-center gap-1.5 min-w-0 rounded-lg border text-xs font-medium transition-colors ${
                // As tall as the workspace picker above it, so the same corners read the same.
                sidebarCollapsed ? 'p-2 justify-center' : 'w-full min-h-[34px] px-3 py-1.5'
              } ${
                // Its status is the chip's colour: red when the service does
                // not answer or is off, yellow when connecting fails.
                chipHealth === 'down' ? 'bg-red-50 border-red-300 text-red-700'
                  : chipHealth === 'failed' ? 'bg-amber-50 border-amber-300 text-amber-800'
                    : PROVIDER_CONFIG[displayModel.provider]?.color || 'bg-gray-50 border-gray-200 text-gray-700'
              }`}
            >
              {/* The icon only on the folded chip, where it is the chip. */}
              {sidebarCollapsed && <Cpu className="w-4 h-4 shrink-0" />}
              {!sidebarCollapsed && (
                <>
                  <span className="min-w-0 flex-1 flex items-center gap-1.5 text-left">
                    <span className="shrink-0">{PROVIDER_CONFIG[displayModel.provider]?.label || displayModel.provider}</span>
                    {displayModel.model && <><span className="opacity-50">·</span><span className="truncate">{displayModel.model}</span></>}
                    {effortShown && <><span className="opacity-50">·</span><span className="shrink-0">{effortLabel(t, effortShown)}</span></>}
                    {/* No "(default)" on the chip; a model inherited from another workspace still says from where. */}
                    {displayModel.source === 'workspace_default' && workspaceModel?.workspace_default?.inherited_from && <span className="opacity-40 italic text-[10px] shrink-0">{t('layout.modelPicker.inheritedSuffix', { workspace: workspaceModel.workspace_default.inherited_from })}</span>}
                  </span>
                  <ChevronDown className="w-3 h-3 opacity-60 shrink-0" />
                </>
              )}
            </button>
            {showModelPicker && (
              <div className={`absolute ${
                narrow ? 'left-0 right-0 top-full mt-1 max-h-[70vh] overflow-y-auto'
                  : sidebarCollapsed ? 'left-full top-0 ml-3 w-[18.4rem]' : 'left-0 top-full mt-1 w-[18.4rem]'
              } bg-gray-100 border border-gray-300 rounded-xl shadow-xl z-50 py-1`}>
                {policy && (
                  <p className="px-4 pt-1.5 pb-0.5 text-[10px] text-gray-400">
                    {policy.models.length === 1 ? t('modelPolicy.picker.onlyModel') : t('modelPolicy.models.policyNote')}
                  </p>
                )}
                {/* The models every member may pick (the enabled catalog
                    models, or the policy's), grouped by provider, with a mark
                    for this workspace's default. No row of its own for the
                    global default: it is one of these models. */}
                {pickerModels.map(({ provider, model }, idx) => {
                  const showHeader = idx === 0 || pickerModels[idx - 1].provider !== provider;
                  const wd = workspaceModel.workspace_default || {};
                  const isDefault = wd.provider === provider && wd.model === model;
                  const isActive = displayModel.provider === provider && displayModel.model === model;
                  // The model the workspace gets anyway clears the picker's
                  // choice rather than pinning it, so the workspace keeps
                  // following its default and the global one.
                  const inherited = inheritedModel.provider === provider && inheritedModel.model === model;
                  return (
                    <div key={`${provider}:${model}`}>
                      {showHeader && (
                        <p className="px-4 pt-1.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                          {PROVIDER_CONFIG[provider]?.label || provider}
                        </p>
                      )}
                      <button
                        onClick={() => (inherited ? handleModelSwitch('', '') : handleModelSwitch(provider, model))}
                        className={`w-full text-left flex items-center gap-2 px-4 py-2 text-xs hover:bg-gray-200 transition-colors ${isActive ? 'bg-indigo-50' : ''}`}
                      >
                        {statusDot(provider)}
                        <span className="truncate text-gray-800 font-mono">{model}</span>
                        {isDefault && (
                          <span className="px-1.5 py-0.5 rounded border text-[9px] font-medium shrink-0 bg-indigo-50 border-indigo-200 text-indigo-600 uppercase tracking-wider">{t('layout.modelPicker.defaultBadge')}</span>
                        )}
                        {isActive && (
                          <span className="ml-auto text-indigo-500 font-bold shrink-0">✓</span>
                        )}
                      </button>
                    </div>
                  );
                })}
                {/* Reasoning effort for agents that inherit the model: the
                    leftmost stop is the model's own default */}
                {displayProfile?.profile?.efforts?.length > 0 && (
                  <div className="border-t border-gray-300 mt-1 px-4 pt-2 pb-1.5">
                    {policy?.effort || member ? (
                      <div
                        className="flex items-baseline justify-between gap-2 opacity-60 cursor-not-allowed"
                        title={policy?.effort ? t('modelPolicy.picker.fixedEffort') : t('modelPolicy.picker.adminEffort')}
                        data-testid="policy-fixed-effort"
                      >
                        <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">{t('effort.headerTitle')}</span>
                        <span className="text-xs font-medium text-gray-600">
                          {effortLabel(t, effortShown || policy?.effort) || effortLabel(t, displayProfile.value) || '—'}
                        </span>
                      </div>
                    ) : (
                    <EffortSlider
                      efforts={displayProfile.profile.efforts}
                      value={effortShown}
                      onChange={handleEffortSwitch}
                      title={t('effort.headerTitle')}
                      hint={t('effort.headerHint')}
                      defaultLabel={t('effort.headerDefault', { value: effortLabel(t, displayProfile.value) || '—' })}
                    />
                    )}
                  </div>
                )}
                <div className="border-t border-gray-300 mt-1 pt-1">
                  <p className="px-4 py-1.5 text-[10px] text-gray-400">
                    {t('layout.modelPicker.legend')} <span className="text-green-600">●</span> {t('layout.modelPicker.legendAvailable')} &nbsp;
                    <span className="text-red-500">●</span> {t('layout.modelPicker.legendUnreachable')} &nbsp;
                    <span className="text-amber-400">●</span> {t('layout.modelPicker.legendFailed')} &nbsp;
                    <span className="text-gray-400">●</span> {t('layout.modelPicker.legendUntested')}
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
        <nav className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden pb-2">
          {menuGroups.map((group, gi) => (
            <div key={group.label}>
              {gi > 0 && <div className="mx-4 my-1 border-t border-gray-100" />}
              {!sidebarCollapsed && (
                <p className="px-6 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400 flex items-center gap-1.5">
                  {group.label}
                  {group.disabled && (
                    <span className="text-[9px] font-semibold bg-gray-100 text-gray-400 border border-gray-200 rounded px-1 py-0.5 leading-none normal-case tracking-normal">
                      {t('nav.comingSoon')}
                    </span>
                  )}
                </p>
              )}
              {sidebarCollapsed && gi === 0 && <div className="pt-3" />}
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = !group.disabled && isItemActive(item, location.pathname);
                if (group.disabled) {
                  return (
                    <div
                      key={item.name}
                      className={`flex items-center py-2.5 text-gray-300 cursor-not-allowed select-none ${
                        sidebarCollapsed ? 'justify-center px-2' : 'px-6'
                      }`}
                      title={sidebarCollapsed ? t('nav.comingSoonItem', { name: item.name }) : t('nav.comingSoonTitle')}
                    >
                      <Icon className={`w-5 h-5 ${sidebarCollapsed ? '' : 'mr-3'}`} />
                      {!sidebarCollapsed && <span className="font-medium text-sm">{item.name}</span>}
                    </div>
                  );
                }
                return (
                  <Link
                    key={item.name}
                    to={item.path}
                    title={sidebarCollapsed ? item.name : undefined}
                    className={`flex items-center py-2.5 text-gray-700 hover:bg-indigo-50 hover:text-indigo-600 transition-colors ${
                      sidebarCollapsed ? 'justify-center px-2' : 'px-6'
                    } ${isActive ? 'bg-indigo-50 text-indigo-600 ' + (sidebarCollapsed ? 'border-l-4 border-indigo-600' : 'border-r-4 border-indigo-600') : ''}`}
                  >
                    <Icon className={`w-5 h-5 ${sidebarCollapsed ? '' : 'mr-3'}`} />
                    {!sidebarCollapsed && <span className="font-medium text-sm">{item.name}</span>}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        {/* The account block, fixed at the foot of the menu: what is running in
            the background (the guided setup, watchers, sync), then the account
            with the live status beside the name, and the notifications at the
            end of its row. The account opens the menu with Profile,
            Personalization (theme, language, simple or full menu), Help,
            installing the app and Sign out. */}
        {/* gap, not space-y: an idle watchers row is display:none and must not
            leave its margin above the account row. */}
        <div className={`relative shrink-0 border-t border-gray-200 flex flex-col gap-2 ${sidebarCollapsed ? 'px-2 pt-1.5 pb-2 items-center' : 'px-3 pt-1.5 pb-2'}`}>
          {(guide?.active && !guide.complete && location.pathname !== '/assistant') && (
            sidebarCollapsed ? (
              <button
                type="button"
                onClick={() => navigate('/assistant?setup=1')}
                title={t('setupGuide.pill', { done: guide.done, total: guide.total })}
                aria-label={t('setupGuide.pill', { done: guide.done, total: guide.total })}
                className="p-2 rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 transition-colors"
              >
                <ListChecks className="w-4 h-4" />
              </button>
            ) : (
              <span className="flex items-center rounded-full border border-indigo-200 bg-indigo-50 text-indigo-700 text-xs font-medium">
                <button
                  type="button"
                  onClick={() => navigate('/assistant?setup=1')}
                  title={t('setupGuide.pill', { done: guide.done, total: guide.total })}
                  className={`flex-1 min-w-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full hover:bg-indigo-100 transition-colors ${guide.can_close ? 'pr-1.5' : ''}`}
                >
                  <ListChecks className="w-3.5 h-3.5 shrink-0" />
                  <span className="truncate">{t('setupGuide.pill', { done: guide.done, total: guide.total })}</span>
                  <span className="ml-auto w-10 h-1 shrink-0 rounded-full bg-indigo-200 overflow-hidden">
                    <span className="block h-full bg-indigo-600"
                      style={{ width: `${guide.total ? (guide.done / guide.total) * 100 : 0}%` }} />
                  </span>
                </button>
                {/* Closable once the required steps are done (common/setup_guide.py). */}
                {guide.can_close && (
                  <button
                    type="button"
                    onClick={() => guideAct('dismiss')}
                    title={t('setupGuide.closeHint')}
                    aria-label={t('setupGuide.close')}
                    className="p-1 mr-1 rounded-full hover:bg-indigo-100 transition-colors"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </span>
            )
          )}
          {/* Active watchers (docs/watchers.md) and the two-way sync with the
              linked hub: each only while there is something to show. */}
          <div className={`empty:hidden ${sidebarCollapsed ? 'flex flex-col items-center gap-2' : 'flex flex-wrap items-center gap-2'}`}>
            <WatchersIndicator className="" panelClassName={footPanel('w-96')} />
            <SyncIndicator className="" panelClassName={footPanel('w-80')} compact={sidebarCollapsed} />
          </div>
          <div className={sidebarCollapsed ? 'flex flex-col-reverse items-center gap-2' : 'flex items-center gap-1'}>
            <AccountMenu
              status={liveStatus}
              onToggleLive={backendOnline ? toggleLiveUpdates : undefined}
              onHelp={openHelp}
              compact={sidebarCollapsed}
              menuMode={menuMode}
              onMenuModeChange={changeMenuMode}
              moreCount={moreCount}
            />
            {/* Notification bell (Plan inbox), at the end of the account row */}
            <NotificationBell
              className=""
              buttonClassName="p-2 rounded-lg hover:bg-gray-100"
              panelClassName={footPanel('w-96')}
            />
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* No top bar: everything it held lives in the menu. A phone keeps a
            slim bar with the button that opens the menu drawer, since the
            drawer is closed until asked for. pt-safe: the status bar of an
            installed app comes on top of it. */}
        {narrow && (
          <div className="bg-white border-b border-gray-200 h-12 box-content pt-safe shrink-0 flex items-center gap-2 px-3 z-30">
            <button
              onClick={toggleSidebar}
              title={t('layout.openMenu')}
              aria-label={t('layout.openMenu')}
              className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 hover:text-indigo-600 transition-colors shrink-0"
            >
              <Menu className="w-5 h-5" />
            </button>
            {/* The page's title (PageHeader, AppBar) is drawn here; a page
                without one leaves the mark and the name. */}
            <div ref={setPhoneTitleSlot} className="peer min-w-0 flex-1 flex items-center gap-1.5 empty:hidden" />
            <img src={LOGO_URL} alt="" className="w-6 h-6 shrink-0 peer-[:not(:empty)]:hidden" />
            <span className="text-base font-bold text-indigo-600 truncate peer-[:not(:empty)]:hidden">{t('layout.serviceName')}</span>
          </div>
        )}
        {/* Data the person synced into this workspace end-to-end encrypted: said
            plainly, so nobody wonders why their records look locked here. */}
        <SyncedDataBanner />
        {/* Help (docs/help.md): the Support agent, opened from the account menu */}
        <HelpPanel open={helpOpen} onOpenChange={setHelpOpen} showButton={false} />
        <main className="flex-1 min-h-0 overflow-y-auto pb-safe">{children}</main>
        {/* The chat that follows the page: a button in the corner everywhere
            but on the Chat page, which is one already. */}
        <PageChatPanel />
        {/* The assistant's wake phrase on every page, when it is chosen on the Assistant page. */}
        <WakeListener />
      </div>
    </div>
    </PhoneTitleSlot.Provider>
  );
};

export default Layout;
