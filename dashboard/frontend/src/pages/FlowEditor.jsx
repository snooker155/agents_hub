import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { AlertTriangle, Download, Factory, Loader2, Play, Save, SquareTerminal } from 'lucide-react';
import { useWorkspace } from '../components/workspace';
import FlowCanvas, { EntityPalette } from '../components/flow/FlowCanvas';
import { AppBar } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { useToast } from '../components/toast';
import { NodeInspector } from '../components/flowEditor/NodeInspector';
import { FlowSettings } from '../components/flowEditor/FlowSettings';
import { GraphStateBlock } from '../components/flowEditor/RuntimeState';
import { useFlowDocument } from '../components/flowEditor/useFlowDocument';
import PageLoader from '../components/PageLoader';

// The flow editor (/flows/:flowId/edit): the graph and nothing that runs it.
// Left, the registry palette (one abstract agent node plus processors,
// conditions, nested runs); center, the canvas; right, the selected node's
// inspector or the graph-level settings. Running, history, chat and logs are
// the run page's (pages/FlowRun.jsx, /flows/:flowId), which the Run button
// opens after saving.
function FlowEditor() {
  const { t } = useI18n();
  const toast = useToast();
  const navigate = useNavigate();
  const { flowId } = useParams();
  const { selectedWorkspace } = useWorkspace();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [flow, setFlow] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [availableAgents, setAvailableAgents] = useState([]);
  const [entitiesByCategory, setEntitiesByCategory] = useState({});
  const [dirty, setDirty] = useState(false);

  const docApi = useFlowDocument({
    availableAgents, dirty, edges, entitiesByCategory, flow, flowId, navigate, nodes,
    selectedNodeId, selectedWorkspace, setAvailableAgents, setDirty, setEdges,
    setEntitiesByCategory, setFlow, setLoading, setNodes, setSaving, t, toast,
  });

  // Unsaved edits survive neither a reload nor closing the tab without a word.
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const openRunPage = async () => {
    if (dirty && !(await docApi.persistFlow())) return;
    navigate(`/flows/${flowId}`);
  };

  if (loading || !flow) {
    return (
      <div className="h-full bg-white"><PageLoader /></div>
    );
  }

  const patchFlow = (patch) => { setFlow((prev) => ({ ...prev, ...patch })); setDirty(true); };
  const selected = docApi.selectedNode;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <AppBar
        icon={Factory}
        title={flow.name}
        subtitle={flow.description}
        backTo="/flows"
        backLabel={t('flowEditor.flows')}
        actions={<>
          <button
            onClick={() => docApi.persistFlow()}
            disabled={saving || !dirty}
            className="flex items-center rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm font-semibold text-gray-700 shadow-sm transition-colors hover:bg-gray-100 disabled:cursor-default disabled:opacity-70"
          >
            {saving
              ? <Loader2 className="mr-2 h-4 w-4 animate-spin text-cyan-500" />
              : <Save className="mr-2 h-4 w-4 text-cyan-500" />}
            {saving ? t('common.saving') : dirty ? t('flowEditor.saveFlow') : t('common.saved')}
          </button>
          <button
            onClick={docApi.handleExportFlow}
            className="flex items-center rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm font-semibold text-gray-700 shadow-sm transition-colors hover:bg-gray-100"
            title={t('flowEditor.exportThisFlowAsA')}
          >
            <Download className="mr-2 h-4 w-4 text-cyan-500" />
            {t('flowEditor.export')}
          </button>
          <button
            onClick={openRunPage}
            className="flex items-center rounded-lg bg-cyan-600 px-3 py-1.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-cyan-700"
            title={dirty ? t('flowEditor.saveAndOpenRunPage') : t('flowEditor.openRunPage')}
            data-testid="flow-open-run-page"
          >
            <Play className="mr-2 h-4 w-4" />
            {t('flowEditor.runPage')}
          </button>
        </>}
      />

      <section className="flex min-h-0 flex-1 overflow-hidden bg-white">
        {/* Left column — registry palette */}
        <div className="hidden w-[300px] shrink-0 flex-col overflow-y-auto border-r border-slate-200 p-3 lg:flex">
          <EntityPalette entitiesByCategory={entitiesByCategory} />
        </div>

        {/* Center — canvas */}
        <div className="relative min-h-0 min-w-0 flex-1">
          <FlowCanvas
            nodes={nodes}
            edges={edges}
            onNodesChange={docApi.handleCanvasNodesChange}
            onEdgesChange={docApi.handleCanvasEdgesChange}
            setEdges={docApi.setCanvasEdges}
            setNodes={docApi.setCanvasNodes}
            setSelectedNodeId={setSelectedNodeId}
          />
        </div>

        {/* Right column — node inspector or graph settings */}
        <div className="flex w-[340px] shrink-0 flex-col overflow-hidden border-l border-slate-200 bg-white">
          <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-2.5">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-100">
              <SquareTerminal className="h-4 w-4 text-slate-600" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-bold text-slate-900">
                {selected ? t('flowEditor.node') : t('flowEditor.graphOverview')}
              </div>
              <div className="truncate text-[11px] text-slate-400">
                {t('flowEditor.graphCounts', { nodes: nodes.length, edges: edges.length })}
              </div>
            </div>
            {selected ? (
              <button
                type="button"
                onClick={() => setSelectedNodeId(null)}
                className="text-[11px] font-medium text-slate-400 transition hover:text-slate-600"
              >
                {t('flowEditor.close')}
              </button>
            ) : null}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {selected ? (
              <NodeInspector
                key={selected.id}
                node={selected}
                isAgent={(selected.data.category || 'agent') === 'agent'}
                agents={docApi.agentOptions}
                onPatch={docApi.updateSelectedNode}
                successors={edges.filter((e) => e.source === selected.id).map((e) => ({
                  id: e.target,
                  label: nodes.find((n) => n.id === e.target)?.data?.label || e.target,
                }))}
              />
            ) : (
              <div className="space-y-3">
                {docApi.unchosenCount > 0 ? (
                  <div className="flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                    <span>{t('flowEditor.unchosenAgents', { count: docApi.unchosenCount })}</span>
                  </div>
                ) : null}
                <GraphStateBlock key={flow.id} flow={flow} nodes={nodes} onPatch={patchFlow} />
                <div className="-mx-3 border-t border-slate-200" />
                <FlowSettings
                  flow={flow}
                  nodes={nodes}
                  onPatch={patchFlow}
                  onSetWebhookSecret={docApi.handleSetWebhookSecret}
                />
                <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                  {t('flowEditor.selectNodeHint')}
                </div>
                {/* Below lg the left column is hidden; the palette moves here. */}
                <div className="lg:hidden">
                  <EntityPalette entitiesByCategory={entitiesByCategory} />
                </div>
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

export default FlowEditor;
