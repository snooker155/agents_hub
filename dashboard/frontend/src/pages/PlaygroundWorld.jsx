import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  Globe2, Loader, Save, AlertTriangle, MapPin, Package, Boxes,
  Gauge, Users, Zap, Scroll, Trophy, Trash2, X, Info, Play, MessagesSquare,
} from 'lucide-react';
import {
  getWorld, updateWorld, deleteWorld,
  getWorldChat, clearWorldChat, stopWorldChat, worldChatUrl,
} from '../api';
import EntityChat from '../components/EntityChat';
import { usePageChat, usePageChatPanel } from '../components/pageChat/pageChat';
import { FILL_COLUMN } from '../components/ChatColumn';
import { useToast } from '../components/toast';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import {
  Card, Field, RecordList, NameList, LineList, ConditionList, EffectList,
  PlaceholderHelp,
} from './playground/world-editor';
import {
  inputClass, BASE_ACTIONS, VALUE_TYPES, ARG_TYPES, blankAction, worldPayload,
  problemText, apiMessage, genericRole, genericGrants,
} from './playground/world-spec';

/**
 * The world editor.
 *
 * A world is the answer to five questions, and the page is in that order
 * because it is the order somebody builds one in: where are we, what is here,
 * what is true of the world, who is here, and what can they do. Actions come
 * last because an action refers to everything above it — a room it may be
 * taken in, an item it moves, a value it changes — and writing them first
 * means writing against names that do not exist yet.
 *
 * Everything is one draft object and one Save. A world is not editable in
 * pieces: a role may only take actions that exist, an action may only change
 * values that are declared, and saving half of that pair would store a world
 * that contradicts itself.
 */

export default function PlaygroundWorld() {
  const { t } = useI18n();
  const { worldId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();

  const [world, setWorld] = useState(null);      // as stored
  const [draft, setDraft] = useState(null);      // as edited
  const [problems, setProblems] = useState({ errors: [], warnings: [] });
  const [scenarios, setScenarios] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [editingAction, setEditingAction] = useState(null);   // {index, action}
  // The chat edits the same world this form does, so a turn can land while
  // something is half-typed here. Collapsible because the form is wide: the
  // action dialog and the twelve-column rows want the whole page when you are
  // filling them in by hand.
  const [chatOpen, setChatOpen] = useState(true);
  const [outOfSync, setOutOfSync] = useState(false);
  // The world as the page last knew it to be stored. A ref rather than the
  // `world` state because the chat now reports every edit as it is made: two
  // turns' worth of events can land inside one render, and a state value read
  // from a closure would still be the one from before the first of them —
  // which would read as "you have unsaved edits" the moment the chat changed
  // anything twice in a row.
  const storedRef = useRef(null);

  const adoptStored = useCallback((data) => {
    storedRef.current = data;
    setWorld(data);
    setProblems({ errors: data.errors || [], warnings: data.warnings || [] });
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const { data } = await getWorld(worldId);
        adoptStored(data);
        setDraft(data);
        setScenarios(data.scenarios || []);
      } catch (e) {
        setError(apiMessage(e, t, t('worlds.loadFailed')));
      } finally {
        setLoading(false);
      }
    })();
  }, [worldId, t, adoptStored]);

  const dirty = useMemo(
    () => !!draft && JSON.stringify(worldPayload(draft)) !== JSON.stringify(worldPayload(world)),
    [draft, world],
  );

  const loadChat = useCallback(() => getWorldChat(worldId), [worldId]);
  const clearChat = useCallback(() => clearWorldChat(worldId), [worldId]);
  const stopChat = useCallback(() => stopWorldChat(worldId), [worldId]);

  /**
   * Every edit the chat makes, as it makes it.
   *
   * The builder reports the world after each of its tools, not only when the
   * turn ends, so a room appears in the form at the moment the chat says it was
   * added — the two halves of the page tell the same story at the same time.
   *
   * Adopted straight into the form when nothing is unsaved — that is the whole
   * point of the chat sitting beside it. Never over unsaved edits, though:
   * silently replacing what somebody just typed is worse than leaving a stale
   * form they can still save or discard, so that case says so instead.
   */
  const onChatEvent = useCallback((ev) => {
    if (ev.type !== 'world' || !ev.world) return;
    const previous = storedRef.current;
    adoptStored(ev.world);
    // The turn's payload is the world, not the page: it carries no scenario
    // list, and clearing the one we have would drop the "cast in" chips for a
    // reason that has nothing to do with them.
    if (ev.world.scenarios) setScenarios(ev.world.scenarios);
    setDraft((current) => {
      const edited = JSON.stringify(worldPayload(current))
        !== JSON.stringify(worldPayload(previous));
      if (edited) {
        setOutOfSync(true);
        return current;
      }
      setOutOfSync(false);
      return ev.world;
    });
  }, [adoptStored]);

  const chatSuggestions = useMemo(() => [
    t('worlds.chatSuggestAddLocation'),
    t('worlds.chatSuggestAddItem'),
    t('worlds.chatSuggestAddAction'),
    t('worlds.chatSuggestRestrict'),
    t('worlds.chatSuggestEnding'),
    t('worlds.chatSuggestExplain'),
  ], [t]);

  // The build chat as one descriptor: the column beside the form and the
  // floating page chat are two frames around this single conversation, so it
  // cannot become two threads about one world.
  const worldChat = useMemo(() => (worldId ? {
    scope: `world:${worldId}`,
    path: worldChatUrl(worldId),
    loadChat, clearChat, stopChat,
    onEvent: onChatEvent,
    title: t('worlds.buildChat'),
    emptyHint: t('worlds.chatHint'),
    suggestions: chatSuggestions,
  } : null), [worldId, loadChat, clearChat, stopChat, onChatEvent, chatSuggestions, t]);
  usePageChat(worldChat);
  const { inlineSuppressed: panelHoldsChat, setOpen: setPanelOpen } = usePageChatPanel();
  // What the layout reacts to: the toggle stays as the user set it, but the
  // second column is only reserved when something is actually drawn in it.
  const chatVisible = chatOpen && !panelHoldsChat && !!worldChat;

  const set = useCallback((patch) => setDraft((d) => ({ ...d, ...patch })), []);

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const { data } = await updateWorld(worldId, worldPayload(draft));
      adoptStored(data);
      setDraft(data);
      setScenarios(data.scenarios || []);
      // Saved *and* incomplete is a real state: a world is built over several
      // sittings, and the alternative — refusing to keep it — is a form people
      // work around rather than in.
      if ((data.errors || []).length) toast.info(t('worlds.savedWithProblems'));
      else toast.success(t('worlds.saved'));
    } catch (e) {
      setError(apiMessage(e, t, t('worlds.saveFailed')));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(scenarios.length
      ? t('worlds.deleteUsedConfirm', { count: scenarios.length })
      : t('worlds.deleteConfirm'))) return;
    try {
      await deleteWorld(worldId, scenarios.length > 0);
      navigate('/playground/worlds');
    } catch (e) {
      setError(apiMessage(e, t, t('worlds.deleteFailed')));
    }
  };

  if (loading) {
    return (
      <PageContainer>
        <div className="flex items-center gap-2 text-sm text-gray-500 py-10">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </div>
      </PageContainer>
    );
  }
  if (!draft) {
    return (
      <PageContainer>
        <div className="text-sm text-red-600 py-10">{error || t('worlds.notFound')}</div>
      </PageContainer>
    );
  }

  // The pools every section suggests from. One derivation, because a name that
  // is offered in one place and unknown in another is exactly the mistake the
  // validator has to report afterwards.
  const locationNames = (draft.locations || []).map((l) => l.name).filter(Boolean);
  const itemNames = (draft.items || []).map((i) => i.name).filter(Boolean);
  const entityNames = (draft.entities || []).map((e) => e.name).filter(Boolean);
  const globalNames = (draft.globals || []).map((g) => g.name).filter(Boolean);
  const statNames = (draft.stats || []).map((s) => s.name).filter(Boolean);
  const roleNames = (draft.roles || []).map((r) => r.name).filter(Boolean);
  // The keys entities actually carry: what `set_entity` may write, and the one
  // pool that is not simply a list of names somewhere in the draft.
  const entityStateKeys = [...new Set((draft.entities || [])
    .flatMap((e) => Object.keys(e.state || {})))];
  const actionNames = [
    ...(draft.base_actions || []),
    ...(draft.actions || []).map((a) => a.name).filter(Boolean),
  ];

  // Everyone a scenario does not cast in one of the roles below. It is not an
  // error state — a scenario written before the world grew a role still runs —
  // so it has a shape, and that shape is decided by fields scattered across
  // this form. Worked out here so the Roles card can show it.
  const generic = genericRole(draft);

  return (
    /* With the chat open beside the form the page stops scrolling and its two
       columns scroll instead: that is the only way the chat can be exactly as
       tall as the space left below the heading. A page that scrolls as a whole
       could only give the chat a height decided in advance — one screen,
       starting wherever the heading happens to end — and its bottom would land
       off-screen. Below lg there is no second column, so the page scrolls the
       ordinary way and none of this applies. */
    <PageContainer className={chatVisible ? 'lg:h-full lg:min-h-0 lg:flex lg:flex-col' : ''}>
      <PageHeader
        icon={Globe2}
        title={draft.name || t('worlds.untitled')}
        description={t('worlds.editorSubtitle')}
        backTo="/playground/worlds"
        backLabel={t('worlds.allWorlds')}
        actions={
          <>
            <button
              /* While the floating panel has this chat, the button is the way
                 back to the column rather than a toggle for one that is not
                 drawn. */
              onClick={() => {
                if (panelHoldsChat) {
                  setPanelOpen(false);
                  setChatOpen(true);
                  return;
                }
                setChatOpen((open) => !open);
              }}
              className={`inline-flex items-center px-3 py-2 text-sm font-medium rounded-lg border ${
                chatOpen
                  ? 'text-indigo-700 bg-indigo-50 border-indigo-200 hover:bg-indigo-100'
                  : 'text-gray-600 bg-white border-gray-300 hover:text-indigo-700 hover:border-indigo-300'
              }`}
            >
              <MessagesSquare className="w-4 h-4 mr-1.5" /> {t('worlds.buildChat')}
            </button>
            <button
              onClick={remove}
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:text-red-600 hover:border-red-300"
            >
              <Trash2 className="w-4 h-4 mr-1.5" /> {t('worlds.deleteWorld')}
            </button>
            <button
              onClick={save} disabled={saving}
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" />
                      : <Save className="w-4 h-4 mr-1.5" />}
              {t('worlds.save')}
            </button>
          </>
        }
      />

      {error && (
        <div className="shrink-0 mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <Problems problems={problems} />

      {/* The form and the chat that edits the same world. Two columns only
          where there is room for both: below lg the chat follows the form
          rather than squeezing a twelve-column row into half a screen. */}
      <div className={chatVisible
        ? 'grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(19rem,25rem)] '
          + 'gap-6 lg:flex-1 lg:min-h-0'
        : ''}>
        <div className={`space-y-6 min-w-0${
          chatVisible ? ' lg:min-h-0 lg:overflow-y-auto lg:pr-1' : ''}`}>
          {/* Both banners live in this column rather than above the grid: the
              unsaved one is sticky, and spanning the row it used to hover over
              the chat's heading as well as the form it is about. */}
          {outOfSync && (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2">
              <span className="text-xs text-sky-800">{t('worlds.changedInChat')}</span>
              <button
                onClick={() => { setDraft(world); setOutOfSync(false); }}
                className="text-xs font-semibold text-sky-700 hover:text-sky-900 underline"
              >
                {t('worlds.discardAndReload')}
              </button>
            </div>
          )}

          {dirty && (
            <div className="sticky top-2 z-10 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 shadow-sm">
              <span className="text-xs text-amber-800">{t('worlds.unsaved')}</span>
              <div className="flex items-center gap-3">
                <button onClick={() => setDraft(world)}
                        className="text-xs font-semibold text-amber-800 underline">
                  {t('worlds.discard')}
                </button>
                <button onClick={save} disabled={saving}
                        className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50">
                  <Save className="w-3.5 h-3.5 mr-1" /> {t('worlds.save')}
                </button>
              </div>
            </div>
          )}

          {/* ── Identity ─────────────────────────────────────────────────── */}
          <Card icon={Info} title={t('worlds.identity')}
                description={t('worlds.identityHint')}>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label={t('worlds.name')}>
                <input value={draft.name || ''} onChange={(e) => set({ name: e.target.value })}
                       className={inputClass} />
              </Field>
              <Field label={t('worlds.envId')} hint={t('worlds.envIdHint')}>
                <input value={draft.env_id || ''} readOnly
                       className={`${inputClass} bg-gray-50 text-gray-500 font-mono text-xs`} />
              </Field>
              <Field label={t('worlds.description')} className="md:col-span-2"
                     hint={t('worlds.descriptionHint')}>
                <textarea rows={2} value={draft.description || ''}
                          onChange={(e) => set({ description: e.target.value })}
                          className={inputClass} />
              </Field>
            </div>

            {scenarios.length > 0 && (
              <div className="mt-4 pt-3 border-t border-gray-100">
                <div className="text-[11px] font-bold text-gray-500 uppercase mb-1.5">
                  {t('worlds.usedBy')}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {scenarios.map((s) => (
                    <Link key={s.scenario_id} to={`/playground/${s.scenario_id}`}
                          className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-100 hover:bg-indigo-100">
                      <Play className="w-3 h-3" /> {s.name || s.scenario_id}
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </Card>

          {/* ── Places ───────────────────────────────────────────────────── */}
          <Card icon={MapPin} title={t('worlds.locations')}
                description={t('worlds.locationsHint')}>
            <RecordList
              rows={draft.locations || []}
              onChange={(locations) => set({ locations })}
              blank={{ name: '', description: '', connects_to: [] }}
              addLabel={t('worlds.addLocation')}
              empty={t('worlds.noLocations')}
              suggestions={{ locations: locationNames }}
              columns={[
                { key: 'name', label: t('worlds.name'), span: 'md:col-span-3' },
                { key: 'description', label: t('worlds.description'), span: 'md:col-span-5' },
                { key: 'connects_to', label: t('worlds.connectsTo'), type: 'names',
                  span: 'md:col-span-4', suggest: 'locations',
                  placeholder: t('worlds.connectsToHint') },
              ]}
            />
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-4 pt-3 border-t border-gray-100">
              <Field label={t('worlds.startingLocation')} hint={t('worlds.startingLocationHint')}>
                <NameList value={draft.starting_location ? [draft.starting_location] : []}
                          options={locationNames}
                          onChange={(v) => set({ starting_location: v[0] || '' })} />
              </Field>
              <Field label={t('worlds.timeOfDay')} hint={t('worlds.timeOfDayHint')}>
                <input value={draft.time_of_day || ''}
                       onChange={(e) => set({ time_of_day: e.target.value })}
                       className={inputClass} />
              </Field>
              <Field label={t('worlds.hoursPerTick')}>
                <input type="number" min={0} value={draft.hours_per_tick ?? 1}
                       onChange={(e) => set({ hours_per_tick: Number(e.target.value) })}
                       className={inputClass} />
              </Field>
            </div>
          </Card>

          {/* ── Things ───────────────────────────────────────────────────── */}
          <Card icon={Package} title={t('worlds.items')} description={t('worlds.itemsHint')}>
            <RecordList
              rows={draft.items || []}
              onChange={(items) => set({ items })}
              blank={{ name: '', description: '', location: '', holder: '', portable: true }}
              addLabel={t('worlds.addItem')}
              empty={t('worlds.noItems')}
              suggestions={{ locations: locationNames, roles: roleNames }}
              columns={[
                { key: 'name', label: t('worlds.name'), span: 'md:col-span-3' },
                { key: 'description', label: t('worlds.description'), span: 'md:col-span-4' },
                { key: 'location', label: t('worlds.startsIn'), span: 'md:col-span-2',
                  suggest: 'locations' },
                { key: 'holder', label: t('worlds.heldBy'), span: 'md:col-span-2',
                  suggest: 'roles', placeholder: t('worlds.heldByHint') },
                { key: 'portable', label: t('worlds.portable'), type: 'boolean',
                  span: 'md:col-span-1' },
              ]}
            />
          </Card>

          <Card icon={Boxes} title={t('worlds.entities')} description={t('worlds.entitiesHint')}>
            <RecordList
              rows={draft.entities || []}
              onChange={(entities) => set({ entities })}
              blank={{ name: '', kind: 'object', description: '', location: '',
                       state: {}, visible: true }}
              addLabel={t('worlds.addEntity')}
              empty={t('worlds.noEntities')}
              suggestions={{ locations: locationNames }}
              columns={[
                { key: 'name', label: t('worlds.name'), span: 'md:col-span-3' },
                { key: 'kind', label: t('worlds.kind'), span: 'md:col-span-2' },
                { key: 'location', label: t('worlds.standsIn'), span: 'md:col-span-2',
                  suggest: 'locations' },
                { key: 'state', label: t('worlds.state'), type: 'pairs',
                  span: 'md:col-span-4', placeholder: t('worlds.stateExample') },
                { key: 'visible', label: t('worlds.visible'), type: 'boolean',
                  span: 'md:col-span-1' },
              ]}
            />
          </Card>

          {/* ── Values ───────────────────────────────────────────────────── */}
          <Card icon={Gauge} title={t('worlds.globals')} description={t('worlds.globalsHint')}>
            <RecordList
              rows={draft.globals || []}
              onChange={(globals) => set({ globals })}
              blank={{ name: '', type: 'number', default: 0, description: '', public: true }}
              addLabel={t('worlds.addGlobal')}
              empty={t('worlds.noGlobals')}
              columns={VALUE_COLUMNS(t)}
            />
          </Card>

          <Card icon={Gauge} title={t('worlds.stats')} description={t('worlds.statsHint')}>
            <RecordList
              rows={draft.stats || []}
              onChange={(stats) => set({ stats })}
              blank={{ name: '', type: 'number', default: 0, description: '', public: true }}
              addLabel={t('worlds.addStat')}
              empty={t('worlds.noStats')}
              columns={VALUE_COLUMNS(t)}
            />
          </Card>

          {/* ── People ───────────────────────────────────────────────────── */}
          <Card icon={Users} title={t('worlds.roles')} description={t('worlds.rolesHint')}>
            <RecordList
              rows={draft.roles || []}
              onChange={(roles) => set({ roles })}
              blank={{ name: '', description: '', actions: [], can_interact_with: [],
                       start_location: '', start_items: [], stats: {} }}
              addLabel={t('worlds.addRole')}
              empty={t('worlds.noRoles')}
              suggestions={{ locations: locationNames, roles: roleNames,
                             actions: actionNames, items: itemNames }}
              columns={[
                { key: 'name', label: t('worlds.name'), span: 'md:col-span-3' },
                { key: 'description', label: t('worlds.description'), span: 'md:col-span-4' },
                { key: 'start_location', label: t('worlds.startsIn'), span: 'md:col-span-2',
                  suggest: 'locations' },
                { key: 'start_items', label: t('worlds.startsWith'), type: 'names',
                  span: 'md:col-span-3', suggest: 'items' },
                { key: 'actions', label: t('worlds.mayTake'), type: 'names',
                  span: 'md:col-span-5', suggest: 'actions',
                  placeholder: t('worlds.mayTakeHint') },
                { key: 'can_interact_with', label: t('worlds.mayActOn'), type: 'names',
                  span: 'md:col-span-4', suggest: 'roles',
                  placeholder: t('worlds.mayActOnHint') },
                { key: 'stats', label: t('worlds.startingStats'), type: 'pairs',
                  span: 'md:col-span-3', placeholder: t('worlds.statsExample') },
              ]}
            />

            {/* The role nobody writes and every world has. A character cast in
                a role this world does not declare is not refused anywhere, so
                what it gets is a real answer — and one assembled from fields
                on three different cards. Reading it off here beats discovering
                it halfway through a run. */}
            <div className="mt-4 rounded-lg border border-dashed border-gray-300 bg-gray-50 p-3">
              <div className="text-[11px] font-bold text-gray-500 uppercase tracking-wide">
                {t('worlds.genericRole')}
              </div>
              <p className="text-[11px] text-gray-500 leading-snug mt-0.5">
                {t('worlds.genericRoleHint')}
              </p>
              {/* Word for word the line the scenario form shows on a character
                  nothing bound. A world with every action reserved leaves this
                  one at zero, which is the whole warning it needs. */}
              <p className={`text-[11px] mt-1.5 ${
                generic.actions.length ? 'text-gray-700' : 'text-amber-700'
              }`}>
                {[t('playground.worldRole.unboundChip'),
                  ...genericGrants(generic, t)].join(' · ')}
              </p>
            </div>
          </Card>

          {/* ── Doing ────────────────────────────────────────────────────── */}
          <Card
            icon={Zap} title={t('worlds.actions')} description={t('worlds.actionsHint')}
            actions={
              <button
                onClick={() => setEditingAction({ index: -1, action: blankAction() })}
                className="text-xs font-semibold text-indigo-600 hover:text-indigo-800"
              >
                {t('worlds.addAction')}
              </button>
            }
          >
            <div className="mb-4">
              <div className="text-[11px] font-bold text-gray-500 uppercase mb-1.5">
                {t('worlds.builtIns')}
              </div>
              <div className="flex flex-wrap gap-2">
                {BASE_ACTIONS.map((name) => {
                  const on = (draft.base_actions || []).includes(name);
                  return (
                    <button
                      key={name}
                      onClick={() => set({
                        base_actions: on
                          ? (draft.base_actions || []).filter((b) => b !== name)
                          : [...(draft.base_actions || []), name],
                      })}
                      className={`px-2 py-1 rounded-md border text-[11px] font-mono ${
                        on ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                           : 'border-gray-200 text-gray-400 hover:bg-gray-50'
                      }`}
                    >
                      {name}
                    </button>
                  );
                })}
              </div>
              <p className="text-[11px] text-gray-400 mt-1.5">{t('worlds.builtInsHint')}</p>
            </div>

            {(draft.actions || []).length === 0 ? (
              <p className="text-xs text-gray-400 italic">{t('worlds.noActions')}</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {(draft.actions || []).map((a, i) => (
                  <ActionCard
                    key={i} action={a}
                    onOpen={() => setEditingAction({ index: i, action: a })}
                    onRemove={() => set({
                      actions: (draft.actions || []).filter((_, j) => j !== i),
                    })}
                  />
                ))}
              </div>
            )}
          </Card>

          {/* ── Telling ──────────────────────────────────────────────────── */}
          <Card icon={Scroll} title={t('worlds.rules')} description={t('worlds.rulesHint')}>
            <LineList value={draft.rules || []} rows={5}
                      onChange={(rules) => set({ rules })}
                      placeholder={t('worlds.rulesPlaceholder')} />
          </Card>

          {/* ── Scoring and ending ───────────────────────────────────────── */}
          <Card icon={Trophy} title={t('worlds.objectives')}
                description={t('worlds.objectivesHint')}>
            <RecordList
              rows={draft.objectives || []}
              onChange={(objectives) => set({ objectives })}
              blank={{ name: '', description: '', source: 'stat', key: '' }}
              addLabel={t('worlds.addObjective')}
              empty={t('worlds.noObjectives')}
              suggestions={{ names: [...statNames, ...globalNames] }}
              columns={[
                { key: 'name', label: t('worlds.name'), span: 'md:col-span-3' },
                { key: 'source', label: t('worlds.scoredFrom'), type: 'select',
                  span: 'md:col-span-3',
                  options: [
                    { value: 'stat', label: t('worlds.sourceStat') },
                    { value: 'items_held', label: t('worlds.sourceItems') },
                    { value: 'locations_visited', label: t('worlds.sourceVisited') },
                    { value: 'global', label: t('worlds.sourceGlobal') },
                  ] },
                { key: 'key', label: t('worlds.whichValue'), span: 'md:col-span-3',
                  suggest: 'names' },
                { key: 'description', label: t('worlds.description'), span: 'md:col-span-3' },
              ]}
            />

            <div className="mt-5 pt-4 border-t border-gray-100">
              <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">
                {t('worlds.ending')}
              </div>
              <p className="text-xs text-gray-500 mb-2">{t('worlds.endingHint')}</p>
              <ConditionList
                conditions={draft.end_when || []}
                onChange={(end_when) => set({ end_when })}
                pools={{ names: [...globalNames, ...statNames, ...entityNames,
                                 ...itemNames] }}
              />
            </div>
          </Card>
        </div>

        {chatVisible && (
          /* As tall as the row, which is as tall as what is left of the screen
             — so the composer sits on the bottom edge of the page rather than
             below it. Beside the form that is the whole column; stacked under
             it there is no such row, so it takes a screen's worth and the page
             scrolls to it. */
          <aside className="h-[70vh] lg:h-auto lg:min-h-0">
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm flex flex-col h-full">
              <EntityChat
                {...worldChat}
                /* Fills the card: no taller than the column, no shorter, and
                   the composer on the floor even when the feed is nearly
                   empty. The three go together (see ChatColumn). */
                {...FILL_COLUMN}
                className="flex-1 min-h-0"
                /* `mt-auto` puts the composer on the card's floor however
                   short the feed is; the negative margins draw its rule the
                   full width of the card instead of stopping at the padding. */
                composerClassName="mt-auto pt-3 -mx-5 px-5"
              />
            </div>
          </aside>
        )}
      </div>

      {editingAction && (
        <ActionDialog
          action={editingAction.action}
          pools={{
            locations: locationNames, items: itemNames, entities: entityNames,
            globals: globalNames, stats: statNames, roles: roleNames,
            stateKeys: entityStateKeys,
          }}
          onClose={() => setEditingAction(null)}
          onSave={(action) => {
            const actions = editingAction.index < 0
              ? [...(draft.actions || []), action]
              : (draft.actions || []).map((a, j) => (j === editingAction.index ? action : a));
            set({ actions });
            setEditingAction(null);
          }}
        />
      )}
    </PageContainer>
  );
}

/** The columns a global and a stat share — they are the same kind of thing. */
const VALUE_COLUMNS = (t) => [
  { key: 'name', label: t('worlds.name'), span: 'md:col-span-2' },
  { key: 'type', label: t('worlds.type'), type: 'select', span: 'md:col-span-2',
    options: ['number', 'integer', 'string', 'boolean'] },
  { key: 'default', label: t('worlds.startingValue'), span: 'md:col-span-2' },
  { key: 'minimum', label: t('worlds.min'), type: 'number', span: 'md:col-span-1' },
  { key: 'maximum', label: t('worlds.max'), type: 'number', span: 'md:col-span-1' },
  { key: 'description', label: t('worlds.description'), span: 'md:col-span-3' },
  { key: 'public', label: t('worlds.public'), type: 'boolean', span: 'md:col-span-1' },
];

function Problems({ problems }) {
  const { t } = useI18n();
  const { errors = [], warnings = [] } = problems;
  if (!errors.length && !warnings.length) return null;
  return (
    // `shrink-0`: on the fixed-height layout this sits in a flex column above
    // the panes, and a long list of problems must push them down rather than
    // be squeezed into an unreadable strip.
    <div className="shrink-0 mb-4 space-y-2">
      {errors.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
          <div className="text-xs font-bold text-amber-800 flex items-center gap-1.5 mb-1">
            <AlertTriangle className="w-3.5 h-3.5" />
            {t('worlds.problemsTitle', { count: errors.length })}
          </div>
          <ul className="text-xs text-amber-800 space-y-0.5 list-disc list-inside">
            {errors.map((e, i) => <li key={i}>{problemText(e, t)}</li>)}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-lg border border-sky-200 bg-sky-50 p-3">
          <ul className="text-xs text-sky-800 space-y-0.5 list-disc list-inside">
            {warnings.map((w, i) => <li key={i}>{problemText(w, t)}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function ActionCard({ action, onOpen, onRemove }) {
  const { t } = useI18n();
  return (
    <div className="rounded-lg border border-gray-200 p-3 hover:border-indigo-300 cursor-pointer bg-white"
         onClick={onOpen}>
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-mono font-bold text-indigo-700 truncate">
          {action.name || t('worlds.unnamedAction')}
        </span>
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          className="p-0.5 text-gray-400 hover:text-red-600 shrink-0"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
      <p className="text-[11px] text-gray-500 mt-1 line-clamp-2">{action.description}</p>
      <div className="flex flex-wrap gap-1 mt-2 text-[10px]">
        {(action.roles || []).length > 0 && (
          <span className="px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-100">
            {(action.roles || []).join(', ')}
          </span>
        )}
        {(action.args || []).map((a) => (
          <span key={a.name} className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 font-mono">
            {a.name}
          </span>
        ))}
        {(action.conditions || []).length > 0 && (
          <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-100">
            {t('worlds.conditionCount', { count: action.conditions.length })}
          </span>
        )}
        {(action.effects || []).length > 0 && (
          <span className="px-1.5 py-0.5 rounded bg-green-50 text-green-700 border border-green-100">
            {t('worlds.effectCount', { count: action.effects.length })}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * One action, edited whole.
 *
 * In a dialog rather than inline because an action is four lists and eight
 * fields, and a row of that laid out in the section would bury the only thing
 * the section is scanned for: what the characters in this world can do.
 */
/**
 * What each effect field may be filled with, for this action.
 *
 * The world's own names, plus the arguments *this* action declares: an effect
 * points at whatever the agent named with ``arg:<name>``, and that reference
 * is only valid for arguments that exist here. Offered by type, because
 * ``arg:text`` as the recipient of a message is a row that resolves to nobody.
 */
function effectPools(action, pools) {
  const args = (action.args || []).filter((a) => a.name);
  const refs = (type) => args.filter((a) => a.type === type).map((a) => `arg:${a.name}`);
  const who = ['actor', ...refs('agent')];
  return {
    who,
    whoAll: [...who, '*'],
    locations: [...pools.locations, ...refs('location')],
    entities: [...pools.entities, ...refs('entity')],
    items: [...pools.items, ...refs('item')],
    globals: pools.globals,
    stats: pools.stats,
    stateKeys: pools.stateKeys || [],
  };
}


function ActionDialog({ action, pools, onClose, onSave }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(action);
  const set = (patch) => setDraft({ ...draft, ...patch });

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 sticky top-0 bg-white z-10">
          <h3 className="text-sm font-bold text-gray-900">
            {draft.name || t('worlds.newAction')}
          </h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label={t('worlds.name')} hint={t('worlds.actionNameHint')}>
              <input value={draft.name || ''} onChange={(e) => set({ name: e.target.value })}
                     className={`${inputClass} font-mono`} />
            </Field>
            <Field label={t('worlds.limitedToRoles')} hint={t('worlds.limitedToRolesHint')}>
              <NameList value={draft.roles || []} options={pools.roles}
                        onChange={(roles) => set({ roles })} />
            </Field>
            <Field label={t('worlds.description')} className="md:col-span-2"
                   hint={t('worlds.actionDescriptionHint')}>
              <textarea rows={2} value={draft.description || ''}
                        onChange={(e) => set({ description: e.target.value })}
                        className={inputClass} />
            </Field>
          </div>

          <div>
            <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">
              {t('worlds.arguments')}
            </div>
            <p className="text-xs text-gray-500 mb-2">{t('worlds.argumentsHint')}</p>
            <RecordList
              rows={draft.args || []}
              onChange={(args) => set({ args })}
              blank={{ name: '', type: 'string', description: '', required: true, choices: [] }}
              addLabel={t('worlds.addArgument')}
              empty={t('worlds.noArguments')}
              columns={[
                { key: 'name', label: t('worlds.name'), span: 'md:col-span-2' },
                { key: 'type', label: t('worlds.type'), type: 'select', span: 'md:col-span-2',
                  options: ARG_TYPES },
                { key: 'description', label: t('worlds.description'), span: 'md:col-span-3' },
                { key: 'choices', label: t('worlds.choices'), type: 'names',
                  span: 'md:col-span-3', placeholder: t('worlds.choicesHint') },
                // Two columns, not one: at one the word wraps into a tall
                // narrow stack that ends up touching the row's delete button.
                { key: 'required', label: t('worlds.required'), type: 'boolean',
                  span: 'md:col-span-2' },
              ]}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label={t('worlds.onlyIn')} hint={t('worlds.onlyInHint')}>
              <NameList value={draft.at_locations || []} options={pools.locations}
                        onChange={(at_locations) => set({ at_locations })} />
            </Field>
            <div className="space-y-1.5 pt-4">
              {[
                ['target_present', t('worlds.targetPresent')],
                ['requires_held_item', t('worlds.requiresHeldItem')],
                ['requires_entity_present', t('worlds.requiresEntityPresent')],
              ].map(([key, label]) => (
                <label key={key} className="flex items-center gap-2 text-xs text-gray-600">
                  <input type="checkbox" checked={!!draft[key]}
                         onChange={(e) => set({ [key]: e.target.checked })}
                         className="rounded border-gray-300 text-indigo-600" />
                  {label}
                </label>
              ))}
            </div>
          </div>

          <div>
            <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">
              {t('worlds.conditions')}
            </div>
            <p className="text-xs text-gray-500 mb-2">{t('worlds.conditionsHint')}</p>
            <ConditionList
              conditions={draft.conditions || []}
              onChange={(conditions) => set({ conditions })}
              pools={{ names: [...pools.globals, ...pools.stats, ...pools.entities,
                               ...pools.items] }}
            />
            <Field label={t('worlds.refusal')} hint={t('worlds.refusalHint')} className="mt-3">
              <input value={draft.refusal || ''} onChange={(e) => set({ refusal: e.target.value })}
                     className={inputClass} />
            </Field>
          </div>

          <div>
            <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">
              {t('worlds.effects')}
            </div>
            <p className="text-xs text-gray-500 mb-2">{t('worlds.effectsHint')}</p>
            <EffectList
              effects={draft.effects || []}
              onChange={(effects) => set({ effects })}
              pools={effectPools(draft, pools)}
            />
          </div>

          {/* Between the effects and the two message fields because it is the
              answer to the same question in all three: what goes in place of
              the braces. */}
          <PlaceholderHelp action={draft.name} args={draft.args || []}
                           globals={pools.globals} stats={pools.stats} />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label={t('worlds.successMessage')} hint={t('worlds.successMessageHint')}>
              <input value={draft.success || ''} onChange={(e) => set({ success: e.target.value })}
                     className={inputClass} />
            </Field>
            <Field label={t('worlds.logLine')} hint={t('worlds.logLineHint')}>
              <input value={draft.log || ''} onChange={(e) => set({ log: e.target.value })}
                     className={inputClass} />
            </Field>
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t border-gray-100">
            <button onClick={onClose}
                    className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
              {t('worlds.cancel')}
            </button>
            <button onClick={() => onSave(draft)}
                    className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700">
              <Save className="w-4 h-4 mr-1.5" /> {t('worlds.applyAction')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
