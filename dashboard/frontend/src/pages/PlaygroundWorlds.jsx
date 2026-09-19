import React, { useState, useEffect, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Globe2, Plus, Trash2, Loader, X, Save, AlertTriangle, MapPin, Users, Zap,
  CheckCircle2, Sparkles,
} from 'lucide-react';
import {
  getWorlds, createWorld, deleteWorld, getWorldTemplates, generateWorld,
} from '../api';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { inputClass, apiMessage } from './playground/world-spec';
import PlaygroundSwitch from './playground/nav';

/**
 * Worlds — the environments a user builds instead of the ones we shipped.
 *
 * The catalogue only. A world is a large thing to edit and a small thing to
 * scan, so a card answers the three questions you would otherwise open it for —
 * how big it is, whether it is finished, and what would break if you deleted it
 * — and the editing lives on the world's own page.
 */

export default function PlaygroundWorlds() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  const [worlds, setWorlds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [showNew, setShowNew] = useState(false);
  const [showGenerate, setShowGenerate] = useState(false);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const { data } = await getWorlds(selectedWorkspace);
      setWorlds(data.worlds || []);
    } catch {
      if (!quiet) setWorlds([]);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [selectedWorkspace]);

  useEffect(() => { load(); }, [load]);
  useLiveRefetch(() => load(true), { type: 'worlds.changed', enabled: liveUpdates });

  const remove = async (e, world) => {
    e.preventDefault();
    e.stopPropagation();
    const used = (world.scenarios || []).length;
    if (used && !window.confirm(t('worlds.deleteUsedConfirm', { count: used }))) return;
    try {
      await deleteWorld(world.world_id, used > 0);
      load();
    } catch (err) {
      setMessage(apiMessage(err, t, t('worlds.deleteFailed')));
    }
  };

  return (
    <PageContainer>
      <PageHeader
        icon={Globe2}
        title={t('worlds.title')}
        description={t('worlds.subtitle')}
        badges={<PlaygroundSwitch active="worlds" />}
        actions={
          <>
            {/* Describing a world is a far shorter way into one than filling
                in eleven lists by hand, so it sits next to the manual path
                rather than behind it. */}
            <button
              onClick={() => setShowGenerate(true)}
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100"
            >
              <Sparkles className="w-4 h-4 mr-1.5" /> {t('worlds.generateWithAi')}
            </button>
            <button
              onClick={() => setShowNew(true)}
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
            >
              <Plus className="w-4 h-4 mr-1.5" /> {t('worlds.newWorld')}
            </button>
          </>
        }
      />

      {message && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {message}
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500 py-10">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </div>
      ) : worlds.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-10 text-center">
          <Globe2 className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-500 max-w-lg mx-auto">{t('worlds.emptyState')}</p>
          <div className="mt-4 flex items-center justify-center gap-2 flex-wrap">
            <button
              onClick={() => setShowGenerate(true)}
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100"
            >
              <Sparkles className="w-4 h-4 mr-1.5" /> {t('worlds.generateWithAi')}
            </button>
            <button
              onClick={() => setShowNew(true)}
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
            >
              <Plus className="w-4 h-4 mr-1.5" /> {t('worlds.newWorld')}
            </button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {worlds.map((w) => (
            <Link
              key={w.world_id}
              to={`/playground/worlds/${w.world_id}`}
              className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:border-indigo-300 hover:shadow transition-colors flex flex-col gap-2"
            >
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-bold text-gray-900 truncate">{w.name}</h3>
                <button
                  onClick={(e) => remove(e, w)}
                  title={t('worlds.deleteWorld')}
                  className="p-1 text-gray-400 hover:text-red-600 shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
              {w.description && (
                <p className="text-xs text-gray-500 line-clamp-2">{w.description}</p>
              )}

              <div className="flex flex-wrap gap-1.5 text-[11px] text-gray-600">
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100">
                  <MapPin className="w-3 h-3 text-gray-400" />
                  {t('worlds.locationCount', { count: (w.locations || []).length })}
                </span>
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100">
                  <Users className="w-3 h-3 text-gray-400" />
                  {t('worlds.roleCount', { count: (w.roles || []).length })}
                </span>
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-100">
                  <Zap className="w-3 h-3 text-gray-400" />
                  {t('worlds.actionCount', { count: (w.actions || []).length })}
                </span>
              </div>

              {/* Finished or not, and what it would cost to delete — the two
                  things a card can say that save opening the world. */}
              <div className="mt-auto pt-1 flex items-center gap-2 text-[11px]">
                {(w.errors || []).length > 0 ? (
                  <span className="inline-flex items-center gap-1 text-amber-700">
                    <AlertTriangle className="w-3 h-3" />
                    {t('worlds.problemCount', { count: w.errors.length })}
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-green-700">
                    <CheckCircle2 className="w-3 h-3" /> {t('worlds.runnable')}
                  </span>
                )}
                {(w.scenarios || []).length > 0 && (
                  <span className="ml-auto text-gray-500">
                    {t('worlds.usedByCount', { count: w.scenarios.length })}
                  </span>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}

      {showNew && (
        <NewWorldModal
          workspace={selectedWorkspace}
          onClose={() => setShowNew(false)}
          onCreated={(world) => navigate(`/playground/worlds/${world.world_id}`)}
        />
      )}

      {showGenerate && (
        <GenerateWorldModal
          workspace={selectedWorkspace}
          onClose={() => setShowGenerate(false)}
          onCreated={(worldId) => navigate(`/playground/worlds/${worldId}`)}
        />
      )}
    </PageContainer>
  );
}

/**
 * A new world: blank, or a copy of a starter.
 *
 * The templates are the documentation. Every part of the spec is exercised by
 * at least one of them, so opening one and reading it teaches more about what a
 * world can express than a blank form with eleven empty sections ever will.
 */
function NewWorldModal({ workspace, onClose, onCreated }) {
  const { t } = useI18n();
  const [templates, setTemplates] = useState([]);
  const [template, setTemplate] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const { data } = await getWorldTemplates();
        setTemplates(data.templates || []);
      } catch { /* blank worlds still work without the starters */ }
    })();
  }, []);

  const submit = async () => {
    if (!name.trim() && !template) { setError(t('worlds.nameRequired')); return; }
    setSaving(true);
    setError('');
    try {
      const { data } = await createWorld({
        template: template || undefined,
        // A template brings its own name; typing one overrides it.
        name: name.trim() || undefined,
        description: description.trim() || undefined,
        workspace,
      });
      onCreated(data);
    } catch (e) {
      setError(apiMessage(e, t, t('worlds.createFailed')));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 sticky top-0 bg-white">
          <h3 className="text-sm font-bold text-gray-900">{t('worlds.newWorld')}</h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          {error && <div className="text-xs text-red-600">{error}</div>}

          <div>
            <label className="block text-xs font-bold text-gray-500 uppercase mb-1.5">
              {t('worlds.startFrom')}
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <button
                onClick={() => setTemplate('')}
                className={`text-left rounded-lg border p-3 ${
                  template === '' ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <div className="text-xs font-bold text-gray-900">{t('worlds.blankWorld')}</div>
                <p className="text-[11px] text-gray-500 mt-0.5">{t('worlds.blankWorldHint')}</p>
              </button>
              {templates.map((tpl) => (
                <button
                  key={tpl.template_id}
                  onClick={() => setTemplate(tpl.template_id)}
                  className={`text-left rounded-lg border p-3 ${
                    template === tpl.template_id
                      ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  <div className="text-xs font-bold text-gray-900">{tpl.name}</div>
                  <p className="text-[11px] text-gray-500 mt-0.5 line-clamp-2">{tpl.description}</p>
                  <div className="text-[10px] text-gray-400 mt-1">
                    {t('worlds.templateMeta', {
                      locations: tpl.locations,
                      roles: (tpl.roles || []).join(', ') || '—',
                      actions: tpl.actions,
                    })}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">
              {t('worlds.name')}
            </label>
            <input value={name} onChange={(e) => setName(e.target.value)}
                   placeholder={template ? t('worlds.nameFromTemplate') : ''}
                   className={inputClass} />
          </div>
          <div>
            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">
              {t('worlds.description')}
            </label>
            <input value={description} onChange={(e) => setDescription(e.target.value)}
                   className={inputClass} />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
              {t('worlds.cancel')}
            </button>
            <button onClick={submit} disabled={saving}
                    className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
              {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
              {t('worlds.create')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}


/**
 * A world from a sentence.
 *
 * The World Builder does the whole job in one run — names the places and how
 * they connect, declares the values the situation turns on, writes the actions
 * that conflict needs, gives it an ending — and persists it itself, so all
 * this has to do is take the description and open what came back.
 *
 * This is the cheapest entry to the feature by a wide margin: a world is
 * eleven lists to fill in by hand and one sentence to describe. What comes
 * back is a normal world, editable in the form and in its own chat.
 */
function GenerateWorldModal({ workspace, onClose, onCreated }) {
  const { t } = useI18n();
  const [requirement, setRequirement] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [limitations, setLimitations] = useState('');

  const submit = async () => {
    if (!requirement.trim() || busy) return;
    setBusy(true);
    setError('');
    setLimitations('');
    try {
      const { data } = await generateWorld({
        requirement: requirement.trim(),
        workspace: workspace || undefined,
      });
      if (data.type === 'world' && data.world_id) {
        onCreated(data.world_id);
        return;
      }
      setLimitations(data.message || t('worlds.generateLimitations'));
    } catch (e) {
      setError(apiMessage(e, t, t('worlds.generateFailed')));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-sm font-bold text-gray-900 flex items-center gap-1.5">
            <Sparkles className="w-4 h-4 text-indigo-500" /> {t('worlds.generateWithAi')}
          </h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          {error && <div className="text-xs text-red-600">{error}</div>}
          {limitations && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 whitespace-pre-wrap">
              {limitations}
            </div>
          )}
          <div>
            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">
              {t('worlds.generateDescribe')}
            </label>
            <textarea
              rows={5}
              value={requirement}
              onChange={(e) => setRequirement(e.target.value)}
              placeholder={t('worlds.generatePlaceholder')}
              disabled={busy}
              className="w-full text-sm border border-gray-300 rounded-md px-3 py-2 resize-y disabled:bg-gray-50"
            />
            <p className="text-[11px] text-gray-400 mt-1">{t('worlds.generateHint')}</p>
          </div>
          <div className="flex items-center justify-end gap-2 pt-1">
            {busy && (
              <span className="mr-auto inline-flex items-center gap-1.5 text-xs text-gray-500">
                <Loader className="w-3.5 h-3.5 animate-spin" /> {t('worlds.generating')}
              </span>
            )}
            <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
              {t('worlds.cancel')}
            </button>
            <button
              onClick={submit} disabled={busy || !requirement.trim()}
              className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              <Sparkles className="w-4 h-4 mr-1.5" /> {t('worlds.generate')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
