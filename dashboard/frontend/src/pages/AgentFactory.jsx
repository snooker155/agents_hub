import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Factory, GitBranch, Plus, Trash2 } from 'lucide-react';
import { createFlow, deleteFlow, listFlows } from '../api';
import { useWorkspace } from '../components/WorkspaceContext';

function formatDate(value) {
  if (!value) return 'No activity yet';
  return new Date(value).toLocaleString();
}

const AgentFactory = () => {
  const navigate = useNavigate();
  const { selectedWorkspace, workspaceFilter } = useWorkspace();
  const [flows, setFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newFactory, setNewFactory] = useState({ name: '', description: '' });

  const loadFlows = async () => {
    setLoading(true);
    try {
      const response = await listFlows(workspaceFilter);
      setFlows(response.data || []);
    } catch (error) {
      console.error('Failed to load factories', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFlows();
  }, [selectedWorkspace]);

  const visibleFlows = useMemo(() => flows, [flows]);

  const handleCreate = async (event) => {
    event.preventDefault();
    if (!newFactory.name.trim()) return;

    setCreating(true);
    try {
      const response = await createFlow({
        name: newFactory.name.trim(),
        description: newFactory.description.trim(),
      });
      navigate(`/factory/${response.data.id}`);
    } catch (error) {
      alert(`Failed to create factory: ${error.response?.data?.detail || error.message}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (flowId) => {
    if (!confirm('Delete this factory?')) return;
    try {
      await deleteFlow(flowId);
      await loadFlows();
    } catch (error) {
      alert(`Failed to delete factory: ${error.response?.data?.detail || error.message}`);
    }
  };

  return (
    <div className="space-y-8">
      <section className="rounded-[28px] border border-slate-200 bg-[radial-gradient(circle_at_top_left,_rgba(14,116,144,0.16),_transparent_32%),linear-gradient(135deg,#f8fafc_0%,#ecfeff_45%,#fefce8_100%)] p-8 shadow-sm">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl space-y-3">
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-200 bg-white/80 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-cyan-700">
              <Factory className="h-3.5 w-3.5" />
              Agent Factory
            </div>
            <h1 className="text-4xl font-black tracking-tight text-slate-900">Factories as visual flows</h1>
            <p className="max-w-xl text-sm leading-6 text-slate-600">
              Build reusable delivery pipelines, attach a shared task context, and run either the whole factory or one agent at a time.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-2xl border border-white/70 bg-white/80 p-4 shadow-sm backdrop-blur">
              <div className="text-xs uppercase tracking-[0.2em] text-slate-400">Visible</div>
              <div className="mt-2 text-3xl font-black text-slate-900">{visibleFlows.length}</div>
            </div>
            <div className="rounded-2xl border border-white/70 bg-white/80 p-4 shadow-sm backdrop-blur">
              <div className="text-xs uppercase tracking-[0.2em] text-slate-400">Workspace</div>
              <div className="mt-2 text-sm font-bold text-slate-900">{selectedWorkspace || 'All'}</div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <form onSubmit={handleCreate} className="space-y-5 rounded-[24px] border border-slate-200 bg-white p-6 shadow-sm">
          <div className="space-y-2">
            <h2 className="text-lg font-bold text-slate-900">New factory</h2>
            <p className="text-sm text-slate-500">Start with a blank canvas, then drag agents into the factory flow.</p>
          </div>
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Name</label>
            <input
              value={newFactory.name}
              onChange={(event) => setNewFactory((current) => ({ ...current, name: event.target.value }))}
              placeholder="Customer onboarding pipeline"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
            />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Description</label>
            <textarea
              value={newFactory.description}
              onChange={(event) => setNewFactory((current) => ({ ...current, description: event.target.value }))}
              rows={4}
              placeholder="What this factory is responsible for."
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
            />
          </div>
          <button
            type="submit"
            disabled={creating}
            className="flex w-full items-center justify-center gap-2 rounded-2xl bg-cyan-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-300"
          >
            <Plus className="h-4 w-4" />
            {creating ? 'Creating...' : 'Create factory'}
          </button>
        </form>

        <div className="p-2">
          <div className="mb-5 flex items-center justify-between gap-4">
            <div>
              <h2 className="text-lg font-bold text-slate-900">Factory list</h2>
              <p className="text-sm text-slate-500">Factories behave like saved flows, scoped to the current workspace when assigned.</p>
            </div>
          </div>

          {loading ? (
            <div className="flex min-h-[280px] items-center justify-center rounded-3xl border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500">
              Loading factories...
            </div>
          ) : visibleFlows.length === 0 ? (
            <div className="flex min-h-[280px] flex-col items-center justify-center gap-3 rounded-3xl border border-dashed border-slate-200 bg-slate-50 p-8 text-center">
              <GitBranch className="h-9 w-9 text-slate-300" />
              <div className="space-y-1">
                <div className="text-sm font-semibold text-slate-900">No factories yet</div>
                <div className="text-sm text-slate-500">Create one on the left, then open it to design the flow.</div>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
              {visibleFlows.map((flow) => (
                <div key={flow.id} className="rounded-lg border border-gray-100 bg-white p-4 shadow-sm transition-shadow hover:shadow-md">
                  <div className="mb-3 flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <Link
                        to={`/factory/${flow.id}`}
                        className="block truncate text-sm font-semibold text-gray-900 hover:text-cyan-600"
                      >
                        {flow.name}
                      </Link>
                      <div className="mt-0.5 line-clamp-2 text-[11px] text-gray-500">
                        {flow.description || 'No description provided.'}
                      </div>
                    </div>
                    <span className="shrink-0 rounded bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-gray-600">
                      {flow.workspace || 'Shared'}
                    </span>
                  </div>

                  <div className="mb-3 grid grid-cols-2 gap-2">
                    <div className="rounded-md border border-blue-100 bg-blue-50 px-2 py-1.5 text-center">
                      <div className="text-[9px] font-semibold uppercase tracking-wide text-blue-500">Nodes</div>
                      <div className="text-xs font-semibold text-blue-900">{flow.nodes?.length || 0}</div>
                    </div>
                    <div className="rounded-md border border-amber-100 bg-amber-50 px-2 py-1.5 text-center">
                      <div className="text-[9px] font-semibold uppercase tracking-wide text-amber-500">Updated</div>
                      <div className="truncate text-xs font-semibold text-amber-900">{formatDate(flow.updated_at)}</div>
                    </div>
                  </div>

                  <div className="flex justify-end">
                    <button
                      onClick={() => handleDelete(flow.id)}
                      className="rounded-lg border border-transparent p-1.5 text-gray-400 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600"
                      title="Delete factory"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
};

export default AgentFactory;
