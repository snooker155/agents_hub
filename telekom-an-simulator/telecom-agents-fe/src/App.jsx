import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  MarkerType,
  Handle,
  Position,
} from "reactflow";
import 'reactflow/dist/style.css'
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Play,
  Trash2,
  Plus,
  Link as LinkIcon,
  Radio,
  Network,
  Wrench,
  Workflow,
} from "lucide-react";
// eslint-disable-next-line no-unused-vars
import { motion } from "framer-motion";

// ===================== API helpers =====================
const API_BASE = "http://127.0.0.1:8000";

async function apiGet(path) {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.headers.get('content-type')?.includes('application/json') ? r.json() : r.text();
}
async function apiPost(path, body, opts={}) {
  const r = await fetch(`${API_BASE}${path}`, {
    method: opts.method || 'POST',
    headers: { 'Content-Type': 'application/json', ...(opts.headers||{}) },
    body: body != null ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  try { return await r.json(); } catch { return null; }
}
async function apiDelete(path) {
  const r = await fetch(`${API_BASE}${path}`, { method: 'DELETE' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  try { return await r.json(); } catch { return null; }
}

// ===================== UI helpers =====================
const STATUS = { HEALTHY: "healthy", DEGRADED: "degraded", DOWN: "down" };
function statusColor(status) {
  switch (status) {
    case STATUS.HEALTHY: return "bg-emerald-500 text-white";
    case STATUS.DEGRADED: return "bg-amber-500 text-white";
    case STATUS.DOWN: return "bg-rose-600 text-white";
    default: return "bg-slate-500 text-white";
  }
}
function dotColor(status) {
  switch (status) {
    case STATUS.HEALTHY: return "bg-emerald-500";
    case STATUS.DEGRADED: return "bg-amber-500";
    case STATUS.DOWN: return "bg-rose-600";
    default: return "bg-slate-500";
  }
}
function humanStatus(status) {
  switch (status) {
    case STATUS.HEALTHY: return "Healthy";
    case STATUS.DEGRADED: return "Degraded";
    case STATUS.DOWN: return "Down";
    default: return status || '—';
  }
}

// Map backend status strings → UI enum
function backStatusToUi(s) {
  const v = String(s || '').toLowerCase();
  if (["ok","healthy","up","normal"].includes(v)) return STATUS.HEALTHY;
  if (["warn","warning","degraded"].includes(v)) return STATUS.DEGRADED;
  if (["error","down","critical","failed","fail"].includes(v)) return STATUS.DOWN;
  return STATUS.HEALTHY;
}

// parse server ts (seconds float or iso)
function parseLogTs(ts) {
  if (ts == null) return new Date();
  const num = Number(ts);
  if (!Number.isNaN(num) && Number.isFinite(num)) {
    const ms = num < 1e12 ? num * 1000 : num;
    return new Date(ms);
  }
  const d = new Date(ts);
  return isNaN(d.getTime()) ? new Date() : d;
}

// ===================== RF custom node =====================
function StatusNode({ data, selected }) {
  const { label, status = STATUS.HEALTHY } = data || {};
  return (
    <motion.div
      className={`rounded-2xl shadow-lg border border-slate-800 p-3 min-w-[160px] bg-slate-900 text-white ${selected ? 'ring-2 ring-sky-400' : ''}`}
      initial={{ scale: 0.96, opacity: 0.95 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 260, damping: 20 }}
    >
      <div className="flex items-center gap-2">
        <div className={`h-3 w-3 rounded-full ${dotColor(status)}`} />
        <span className="font-medium text-sm text-white">{label || 'Node'}</span>
      </div>
      <div className="mt-2 text-[11px] text-white uppercase tracking-wide">{humanStatus(status)}</div>
      <Handle type="target" position={Position.Left} className="!bg-slate-300 !border !border-slate-700" />
      <Handle type="source" position={Position.Right} className="!bg-slate-300 !border !border-slate-700" />
    </motion.div>
  );
}
const rfNodeTypes = { statusNode: StatusNode };

// ===================== Main App =====================
export default function App() {
  // RF graph state
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  // Selection
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState([]);

  // Per-node data caches
  const [nodeStatus, setNodeStatus] = useState({}); // {id: status}
  const [nodeTypeMap, setNodeTypeMap] = useState({});  // {id: type}
  const [availableFaults, setAvailableFaults] = useState({}); // {id: {code: desc}}
  const [activeFault, setActiveFault] = useState({}); // {id: faultName|null}
  const [nodeMetrics, setNodeMetrics] = useState({}); // {id: {traffic_speed, traffic_volume}}

  // UI state
  const [faultSingle, setFaultSingle] = useState("");
  const [faultBulk, setFaultBulk] = useState("");
  const [agentRunning, setAgentRunning] = useState(false); // per selected node (local mirror)
  const [agentLogs, setAgentLogs] = useState([]); // [{ts, scope, reasoning, error, status, ...}]
  const [agentReasoning, setAgentReasoning] = useState("");
  const [logLimit, setLogLimit] = useState(200);
  const [newNodeId, setNewNodeId] = useState("");
  const [newNodeType, setNewNodeType] = useState("router");

  // WebSocket ref
  const wsRef = useRef(null);

  // ---------- Topology loader ----------
  const loadTopology = useCallback(async () => {
    try {
      let topo = await apiGet('/topology');
      if (typeof topo === 'string') topo = JSON.parse(topo);

      // Backend format:
      // { nodes: ["router1","core1","n1"], links: { core1: ["router1"], router1: [], n1: [] } }
      let nodesArr = topo && Array.isArray(topo.nodes) ? topo.nodes : [];

      // Fallback to /nodes if /topology empty
      if (!nodesArr.length) {
        const raw = await apiGet('/nodes');
        if (Array.isArray(raw)) nodesArr = raw.map(n => (n.id || n.node_id || n.name)).filter(Boolean);
      }

      // Build ReactFlow nodes (string IDs → positions)
      const rfNodes = nodesArr.map((n, idx) => {
        const id = typeof n === 'string' ? n : (n.id || n.node_id || n.name || String(idx+1));
        const label = typeof n === 'string' ? n : (n.label || n.name || id);
        const pos = (typeof n === 'object' && n && (n.position || n.pos)) || { x: (idx % 5) * 160 + 60, y: Math.floor(idx / 5) * 120 + 80 };
        return { id: String(id), position: pos, data: { label: String(label), status: nodeStatus[id] || STATUS.HEALTHY }, type: 'statusNode' };
      });

      // Build ReactFlow edges from adjacency map
      let rfEdges = [];
      const linksMap = topo && topo.links ? topo.links : {};
      if (linksMap && typeof linksMap === 'object' && !Array.isArray(linksMap)) {
        Object.entries(linksMap).forEach(([src, targets]) => {
          (targets || []).forEach(t => {
            if (src && t) rfEdges.push({ id: `e-${src}-${t}`, source: String(src), target: String(t) });
          });
        });
      } else if (Array.isArray(linksMap)) {
        rfEdges = linksMap.map((e, i) => {
          const a = e.source || e.a || e.from || e.u || e[0];
          const b = e.target || e.b || e.to || e.v || e[1];
          return { id: e.id || `e-${a}-${b}-${i}`, source: String(a), target: String(b) };
        }).filter(e => e.source && e.target);
      }

      setNodes(prev => (JSON.stringify(prev) === JSON.stringify(rfNodes) ? prev : rfNodes));
      setEdges(prev => (JSON.stringify(prev) === JSON.stringify(rfEdges) ? prev : rfEdges));

      // Hydrate per-node details via /nodes/{id}
      try {
        const ids = rfNodes.map(n => n.id);
        const results = await Promise.allSettled(ids.map(id => apiGet(`/nodes/${id}`)));
        const nt = {}, ns = {}, af = {}, nm = {};
        results.forEach((res, idx) => {
          if (res.status !== 'fulfilled' || !res.value) return;
          const id = ids[idx];
          const nd = res.value;
          const t = nd.type || nd.node_type; if (t) nt[id] = t;
          const s = nd.status || nd.state; if (s) ns[id] = backStatusToUi(s);
          if (nd.error) af[id] = nd.error;
          if (nd.traffic_speed != null || nd.traffic_volume != null) nm[id] = { traffic_speed: nd.traffic_speed || 0, traffic_volume: nd.traffic_volume || 0 };
        });
        if (Object.keys(nt).length) setNodeTypeMap(prev => ({ ...prev, ...nt }));
        if (Object.keys(ns).length) setNodeStatus(prev => ({ ...prev, ...ns }));
        if (Object.keys(af).length) setActiveFault(prev => ({ ...prev, ...af }));
        if (Object.keys(nm).length) setNodeMetrics(prev => ({ ...prev, ...nm }));
      } catch (err) {
        console.warn('Could not hydrate node details', err);
      }
    } catch (e) {
      console.error('loadTopology failed', e);
    }
  }, []);

  useEffect(() => { loadTopology(); }, [loadTopology]);

  // keep RF node.data.status in sync with nodeStatus
  useEffect(() => {
    setNodes(nds => {
      let changed = false;
      const next = nds.map(n => {
        const st = nodeStatus[n.id] || STATUS.HEALTHY;
        if (!n.data || n.data.status !== st) {
          changed = true;
          return { ...n, data: { ...(n.data || {}), status: st } };
        }
        return n;
      });
      return changed ? next : nds;
    });
  }, [nodeStatus, setNodes]);

  // ---------- Node details when selected ----------
  useEffect(() => {
    let stop = false;
    async function loadNodeDetails(id) {
      try {
        if (!id) return;
        // status
        try {
          const st = await apiGet(`/nodes/${id}/status`);
          if (!stop) setNodeStatus(prev => ({ ...prev, [id]: backStatusToUi(st.status || st || STATUS.HEALTHY) }));
        } catch (e) { console.warn('Failed to load status', e); }
        // node details (new schema)
        try {
          const nd = await apiGet(`/nodes/${id}`);
          if (nd) {
            const t = nd.type || nd.node_type;
            if (t && !stop) setNodeTypeMap(prev => ({ ...prev, [id]: t }));
            const s = nd.status || nd.state;
            if (s && !stop) setNodeStatus(prev => ({ ...prev, [id]: backStatusToUi(s) }));
            if (nd.error !== undefined && !stop) setActiveFault(prev => ({ ...prev, [id]: nd.error || null }));
            if (!stop) setNodeMetrics(prev => ({ ...prev, [id]: { traffic_speed: nd.traffic_speed || 0, traffic_volume: nd.traffic_volume || 0 } }));
          }
        } catch (e) { console.warn('Failed to load node details', e); }
        // available faults (supports map format { code: description })
        try {
          let faults = await apiGet(`/nodes/${id}/faults`);
          if (typeof faults === 'string') faults = JSON.parse(faults);
          if (!stop) {
            if (faults && typeof faults === 'object' && !Array.isArray(faults)) {
              setAvailableFaults(prev => ({ ...prev, [id]: faults }));
              const firstKey = Object.keys(faults)[0] || "";
              setFaultSingle(firstKey);
            } else if (Array.isArray(faults)) {
              const map = {}; faults.forEach(code => { map[code] = code; });
              setAvailableFaults(prev => ({ ...prev, [id]: map }));
              setFaultSingle(faults[0] || "");
            } else if (nodeTypeMap[id]) {
              let byType = await apiGet(`/faults?node_type=${encodeURIComponent(nodeTypeMap[id])}`);
              if (typeof byType === 'string') byType = JSON.parse(byType);
              if (byType && typeof byType === 'object' && !Array.isArray(byType)) {
                setAvailableFaults(prev => ({ ...prev, [id]: byType }));
                const fk = Object.keys(byType)[0] || "";
                setFaultSingle(fk);
              } else if (Array.isArray(byType)) {
                const map2 = {}; byType.forEach(code => { map2[code] = code; });
                setAvailableFaults(prev => ({ ...prev, [id]: map2 }));
                setFaultSingle(byType[0] || "");
              }
            }
          }
        } catch (e) { console.warn('Failed to load faults', e); }
        // logs initial
        try { await refreshLogs(id); } catch (e) { console.warn('Failed to refresh logs', e); }
      } catch (e) { console.error('loadNodeDetails', e); }
    }
    loadNodeDetails(selectedNodeId);
    return () => { stop = true; };
  }, [selectedNodeId]);

  // ---------- Logs & Reasoning ----------
  async function refreshLogs(id) {
    if (!id) return;
    try {
      const qs = `?limit=${logLimit}`;
      let logs = await apiGet(`/nodes/${id}/logs${qs}`);
      if (typeof logs === 'string') logs = JSON.parse(logs);
      if (Array.isArray(logs)) {
        const mapped = logs.map(l => {
          const t = parseLogTs(l.ts ?? l.timestamp);
          const statusUi = backStatusToUi(l.status);
          return {
            ts: t.toISOString(),
            scope: l.node_id || id,
            node_type: l.node_type,
            status: statusUi,
            traffic_speed: l.traffic_speed,
            traffic_volume: l.traffic_volume,
            error: l.error,
            reasoning: l.reasoning || l.message || l.text,
          };
        });
        setAgentLogs(mapped);
        setAgentReasoning(
          mapped.slice(0, 5).map(l => `• ${l.reasoning || l.error || ''}`).join('')
        );
      }
    } catch (e) { console.warn('refreshLogs failed', e); }
  }

  // Relisten WebSocket on node/limit change
  useEffect(() => {
    if (wsRef.current) { try { wsRef.current.close(); } catch (e) { console.warn('WS close failed', e); } wsRef.current = null; }
    const qs = new URLSearchParams();
    if (selectedNodeId) qs.set('node_id', selectedNodeId);
    qs.set('history', String(logLimit));
    const url = `ws://127.0.0.1:8000/ws?${qs.toString()}`;
    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          const toRec = (l) => {
            const t = parseLogTs(l.ts ?? l.timestamp);
            const st = backStatusToUi(l.status);
            return {
              ts: t.toISOString(),
              scope: l.node_id || selectedNodeId || 'network',
              node_type: l.node_type,
              status: st,
              traffic_speed: l.traffic_speed,
              traffic_volume: l.traffic_volume,
              error: l.error,
              reasoning: l.reasoning || l.message || l.text,
            };
          };
          if (Array.isArray(msg)) {
            const mapped = msg.map(toRec);
            setAgentLogs(prev => [...mapped, ...prev].slice(0, 2000));
          } else if (msg && (msg.error || msg.reasoning || msg.message || msg.text)) {
            setAgentLogs(prev => [toRec(msg), ...prev].slice(0, 2000));
          } else if (typeof msg === 'object' && msg.type === 'status' && msg.node_id) {
            setNodeStatus(prev => ({ ...prev, [msg.node_id]: backStatusToUi(msg.status || prev[msg.node_id]) }));
            setNodeMetrics(prev => ({ ...prev, [msg.node_id]: { traffic_speed: msg.traffic_speed ?? (prev[msg.node_id]?.traffic_speed || 0), traffic_volume: msg.traffic_volume ?? (prev[msg.node_id]?.traffic_volume || 0) } }));
          } else if (typeof msg === 'object' && msg.type === 'state' && msg.nodes) {
            const newStatuses = {};
            const newMetrics = {};
            Object.entries(msg.nodes).forEach(([nid, nd]) => {
              newStatuses[nid] = backStatusToUi(nd.status);
              newMetrics[nid] = {
                traffic_speed: nd.traffic_speed || 0,
                traffic_volume: nd.traffic_volume || 0,
              };
            });
            setNodeStatus(prev => ({ ...prev, ...newStatuses }));
            setNodeMetrics(prev => ({ ...prev, ...newMetrics }));
          }
        } catch {
          const rec = { ts: new Date().toISOString(), scope: selectedNodeId || 'network', reasoning: String(ev.data), status: STATUS.HEALTHY };
          setAgentLogs(prev => [rec, ...prev].slice(0, 2000));
        }
      };
      ws.onerror = (e) => { console.warn('WS error', e); };
      ws.onclose = () => { console.log('WS closed'); };
    } catch (e) { console.warn('ws connect failed', e); }
    return () => { if (wsRef.current) { try { wsRef.current.close(); } catch (e) { console.warn('WS close failed', e); } wsRef.current = null; } };
  }, [selectedNodeId, logLimit]);

  // ---------- RF handlers ----------
  const onNodeClick = useCallback((_, node) => setSelectedNodeId(node.id), []);
  const onSelectionChange = useCallback(({ nodes: selNodes }) => {
    const ids = (selNodes || []).map(n => n.id);
    setSelectedNodeIds(ids);
    if (ids.length === 1) setSelectedNodeId(ids[0]);
  }, []);

  const onConnect = useCallback(async (connection) => {
    setEdges(eds => addEdge(connection, eds)); // optimistic
    try { await apiPost(`/links/${connection.source}/${connection.target}`); }
    catch (e) { console.error('add link failed', e); loadTopology(); }
  }, [setEdges, loadTopology]);

  // ---------- Topology actions ----------
  const addNewNode = async () => {
    const id = (newNodeId || `node-${Math.random().toString(36).slice(2,7)}`).trim();
    const type = newNodeType || 'router';
    try {
      try { await apiPost('/nodes/add', { id, type }); }
      catch (e) {
        console.warn('apiPost /nodes/add failed, trying /nodes', e);
        await apiPost('/nodes', { id, type });
      }
    } catch (e) {
      console.error('Add node failed', e);
    } finally {
      setNewNodeId('');
      await loadTopology();
    }
  };

  const deleteSelectedNode = async () => {
    if (!selectedNodeId) return;
    try { await apiDelete(`/nodes/${selectedNodeId}`); }
    catch (e) { console.error('delete node failed', e); }
    finally { setSelectedNodeId(null); await loadTopology(); }
  };

  const addLinkToSelected = async (pendingSource, targetId) => {
    if (!pendingSource || !targetId || pendingSource === targetId) return;
    try { await apiPost(`/links/${pendingSource}/${targetId}`); }
    catch (e) { console.error('add link failed', e); }
    finally { loadTopology(); }
  };

  const deleteSelectedEdge = async () => {
    if (!selectedNodeId) return;
    const e = edges.find(ed => ed.source === selectedNodeId || ed.target === selectedNodeId);
    if (!e) return;
    try { await apiDelete(`/links/${e.source}/${e.target}`); }
    catch (err) { console.error('delete link failed', err); }
    finally { await loadTopology(); }
  };

  // ---------- Faults & status ----------
  async function applyFaultToNode(nodeId, faultCode) {
    if (!nodeId || !faultCode) return;
    try { await apiPost(`/nodes/${nodeId}/fault`, { type: faultCode }); }
    catch (e) { console.error('apply fault', e); }
    finally {
      try {
        const nd = await apiGet(`/nodes/${nodeId}`);
        if (nd) {
          if (nd.status) setNodeStatus(prev => ({ ...prev, [nodeId]: backStatusToUi(nd.status) }));
          setActiveFault(prev => ({ ...prev, [nodeId]: nd.error || null }));
          setNodeMetrics(prev => ({ ...prev, [nodeId]: { traffic_speed: nd.traffic_speed || 0, traffic_volume: nd.traffic_volume || 0 } }));
        }
      } catch (e) { console.warn('Failed to refresh node data after fault', e); }
      try {
        const st = await apiGet(`/nodes/${nodeId}/status`);
        if (st) setNodeStatus(prev => ({ ...prev, [nodeId]: backStatusToUi(st.status || st) }));
      } catch (e) { console.warn('Failed to refresh status after fault', e); }
      try { await refreshLogs(nodeId); } catch (e) { console.warn('Failed to refresh logs after fault', e); }
    }
  }

  async function applyFaultToSelected(faultCode) {
    if (!faultCode || selectedNodeIds.length === 0) return;
    await Promise.all(selectedNodeIds.map(id => apiPost(`/nodes/${id}/fault`, { type: faultCode }).catch((e) => console.warn(e))));
    await loadTopology();
  }

  async function resolveFault(nodeId) {
    if (!nodeId) return;
    try { await apiDelete(`/nodes/${nodeId}/fault`); }
    catch (e) { console.error('resolve fault', e); }
    finally {
      try {
        const st = await apiGet(`/nodes/${nodeId}/status`);
        if (st) setNodeStatus(prev => ({ ...prev, [nodeId]: backStatusToUi(st.status || st) }));
      } catch (e) { console.warn('Failed to refresh status after resolving fault', e); }
      await loadTopology();
    }
  }

  async function resolveFaultSelected() {
    if (selectedNodeIds.length === 0) return;
    await Promise.all(selectedNodeIds.map(id => apiDelete(`/nodes/${id}/fault`).catch((e) => console.warn(e))));
    await loadTopology();
  }

  // ---------- Agent actions ----------
  const toggleSimulate = async () => {
    if (!selectedNodeId) return;
    const enable = !agentRunning;
    try { await apiPost(`/nodes/${selectedNodeId}/simulate?enabled=${enable}`, null); }
    catch (e) { console.error('simulate toggle', e); }
    finally { setAgentRunning(enable); }
  };

  const runDiagnostic = async () => {
    if (selectedNodeId) {
      try { await apiGet(`/nodes/${selectedNodeId}/status`); await refreshLogs(selectedNodeId); }
      catch (e) { console.error('diag', e); }
    } else {
      try { await loadTopology(); }
      catch (e) { console.warn('Diagnostic loadTopology failed', e); }
    }
  };

  // ---------- Derived UI ----------
  const selectedNode = useMemo(() => nodes.find(n => n.id === selectedNodeId) || null, [nodes, selectedNodeId]);
  useEffect(() => {
    if (selectedNodeId) {
      const map = availableFaults[selectedNodeId] || {};
      const firstKey = Object.keys(map)[0] || "";
      setFaultSingle(firstKey);
    } else {
      setFaultSingle("");
    }
  }, [selectedNodeId, availableFaults]);

  const filteredLogs = useMemo(() => (selectedNodeId ? agentLogs.reverse().filter(l => l.scope === selectedNodeId) : agentLogs), [agentLogs, selectedNodeId]);
  const reasoningView = useMemo(() => {
    if (selectedNodeId) {
      const lines = filteredLogs.slice(0, 20).map(
        l => `${new Date(l.ts).toLocaleTimeString()} • ${l.reasoning || l.error || ''} \n`
      );
      return lines.join('') || '—';
    }
    return agentReasoning || '—';
  }, [filteredLogs, selectedNodeId, agentReasoning]);

  const commonFaultEntries = useMemo(() => {
    if (selectedNodeIds.length === 0) return [];
    let accKeys = null;
    selectedNodeIds.forEach(id => {
      const map = availableFaults[id] || {};
      const keys = Object.keys(map);
      accKeys = accKeys === null ? keys : accKeys.filter(k => keys.includes(k));
    });
    const keys = accKeys || [];
    const firstId = selectedNodeIds.find(id => availableFaults[id]) || null;
    return keys.map(k => [k, firstId ? (availableFaults[firstId][k] || k) : k]);
  }, [selectedNodeIds, availableFaults]);

  useEffect(() => {
    if (selectedNodeIds.length > 1) {
      const codes = commonFaultEntries.map(([code]) => code);
      setFaultBulk(prev => (codes.includes(prev) ? prev : (codes[0] || "")));
    } else {
      setFaultBulk("");
    }
  }, [selectedNodeIds, commonFaultEntries]);

  // ---------- Styles ----------
  const btnSolid = "bg-slate-800 border border-slate-700 !text-white hover:bg-slate-700 focus:!outline-none focus:!ring-0 focus-visible:!outline-none focus-visible:!ring-0 active:opacity-90";
  const btnOutlineDark = "border border-slate-700 !text-white hover:bg-slate-800 hover:!text-white focus:!outline-none focus:!ring-0 focus-visible:!outline-none focus-visible:!ring-0 active:opacity-90";
  const btnGhostDark = "!text-white hover:bg-slate-800 hover:!text-white focus:!outline-none focus:!ring-0 focus-visible:!outline-none focus-visible:!ring-0 active:opacity-90";

  // ---------- Render ----------
  return (
    <div className="h-screen w-screen bg-slate-950 text-white">
      <div className="h-14 border-b bg-slate-900 border-slate-800 flex items-center px-4 gap-3">
        <Network className="h-5 w-5" />
        <span className="font-semibold">Telecom Network Ops</span>
      </div>

      <div className="grid grid-cols-12 gap-3 p-3 h-[calc(100vh-56px)]">
        {/* Left: ReactFlow Canvas */}
        <Card className="col-span-8 overflow-hidden bg-slate-900 border-slate-800 text-white">
          <CardHeader className="py-3">
            <CardTitle className="flex items-center gap-2 text-white"><Workflow className="h-5 w-5"/> Network topology</CardTitle>
            <div className="text-xs text-slate-300/80">Data from API {API_BASE}. Flow: <span className="text-sky-300">source → target</span></div>
          </CardHeader>
          <CardContent className="h-[calc(100%-52px)] p-0 text-white">
            <div className="h-full">
              <ReactFlow
                nodes={nodes}
                edges={edges.map(e => ({
                  ...e,
                  type: e.type || 'smoothstep',
                  style: { ...(e.style||{}), stroke: '#e2e8f0', strokeWidth: 2 },
                  markerEnd: { type: MarkerType.ArrowClosed, width: 10, height: 10, color: '#e2e8f0' },
                }))}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                nodeTypes={rfNodeTypes}
                onNodeClick={onNodeClick}
                onSelectionChange={onSelectionChange}
                onPaneClick={() => { setSelectedNodeId(null); setSelectedNodeIds([]); }}
                selectionOnDrag
                fitView
                defaultEdgeOptions={{ style: { stroke: '#e2e8f0', strokeWidth: 2 }, markerEnd: { type: MarkerType.ArrowClosed, width: 10, height: 10, color: '#e2e8f0' } }}
                connectionLineStyle={{ stroke: '#e2e8f0', strokeWidth: 2 }}
              >
                <Background color="#334155" />
                <MiniMap
                  pannable
                  zoomable
                  className="!border !border-slate-700 !rounded-xl"
                  nodeColor={() => '#334155'}
                  nodeStrokeColor={() => '#475569'}
                  nodeStrokeWidth={2}
                  maskColor="rgba(2,6,23,0.92)"
                  style={{ backgroundColor: '#0b1220' }}
                />
                <Controls
                  className="!border !border-slate-700 !rounded-xl [&_button]:!bg-slate-800 [&_button]:!text-white [&_button]:!border [&_button]:!border-slate-700 [&_button:hover]:!bg-slate-700 [&_button]:focus-visible:!outline-none [&_button]:focus-visible:!ring-0"
                  style={{ backgroundColor: 'rgba(30,41,59,0.85)', color: '#fff' }}
                />
              </ReactFlow>
            </div>
          </CardContent>
        </Card>

        {/* Right: Side panels */}
        <div className="col-span-4 flex flex-col gap-3 min-h-0">
          {/* Network Overview & Topology Controls */}
          <Card className="bg-slate-900 border-slate-800 text-white">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-white"><Activity className="h-5 w-5"/> Network overview</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-white">
              {/* KPIs calculated based on node statuses */}
              {(() => {
                const ids = nodes.map(n => n.id);
                let healthy=0, degraded=0, down=0;
                ids.forEach(id => { const s = nodeStatus[id] || STATUS.HEALTHY; if (s===STATUS.HEALTHY) healthy++; else if (s===STATUS.DEGRADED) degraded++; else down++; });
                const total = Math.max(ids.length,1);
                const availability = Math.round((healthy/total)*100);
                return (
                  <>
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="rounded-2xl p-3 bg-emerald-500/10"><div className="text-2xl font-semibold">{healthy}</div><div className="text-xs text-white flex items-center justify-center gap-1"><CheckCircle2 className="h-3 w-3"/>Healthy</div></div>
                      <div className="rounded-2xl p-3 bg-amber-500/10"><div className="text-2xl font-semibold">{degraded}</div><div className="text-xs text-white flex items-center justify-center gap-1"><AlertTriangle className="h-3 w-3"/>Degraded</div></div>
                      <div className="rounded-2xl p-3 bg-rose-500/10"><div className="text-2xl font-semibold">{down}</div><div className="text-xs text-white flex items-center justify-center gap-1"><Radio className="h-3 w-3"/>Down</div></div>
                    </div>
                    <div className="flex items-center justify-between"><div className="text-sm text-white">Availability</div><div className="font-semibold">{availability}%</div></div>
                    <div className="h-2 bg-slate-800 rounded-full overflow-hidden"><div className="h-full bg-emerald-500" style={{ width: `${availability}%` }} /></div>
                  </>
                );
              })()}

<div className="grid grid-cols-2 gap-2">
  <Input
    value={newNodeId}
    onChange={(e) => setNewNodeId(e.target.value)}
    placeholder="Node ID (optional)"
    className="bg-slate-900 border-slate-800 text-white placeholder:text-slate-400"
  />
  <Select value={newNodeType} onValueChange={setNewNodeType}>
    <SelectTrigger className="text-white"><SelectValue placeholder="Type" /></SelectTrigger>
    <SelectContent className="bg-slate-900 text-white border-slate-800">
      <SelectItem value="router" className="text-white">router</SelectItem>
      <SelectItem value="switch" className="text-white">switch</SelectItem>
      <SelectItem value="access_point" className="text-white">access_point</SelectItem>
    </SelectContent>
  </Select>
</div>
              <div className="pt-1 border-t mt-2 border-slate-800" />
              <div className="grid grid-cols-2 gap-2">
                <Button onClick={addNewNode} className={`w-full ${btnSolid}`} variant="default"><Plus className="mr-2 h-4 w-4"/>Add node</Button>
                <Button onClick={deleteSelectedNode} className={`w-full text-white bg-rose-600 hover:bg-rose-700 focus:!outline-none focus:!ring-0 focus-visible:!outline-none focus-visible:!ring-0`} variant="destructive"><Trash2 className="mr-2 h-4 w-4"/>Delete node</Button>
                {/* Manual connection: select node A, then node B and click */}
                <Button onClick={() => addLinkToSelected(selectedNodeIds[0], selectedNodeIds[1])} disabled={selectedNodeIds.length!==2} className={`w-full ${btnOutlineDark}`}>
                  <LinkIcon className="mr-2 h-4 w-4"/>Add link (A→B)
                </Button>
                <Button onClick={deleteSelectedEdge} className={`w-full ${btnOutlineDark}`} variant="default"><Trash2 className="mr-2 h-4 w-4"/>Delete link</Button>
              </div>

              {/* Bulk fault apply when multi-selected */}
              {selectedNodeIds.length > 1 && (
                <div className="mt-3 rounded-xl border border-slate-800 p-3 bg-slate-900/60">
                  <div className="text-xs uppercase tracking-wide text-white/80 mb-2">Bulk fault assignment ({selectedNodeIds.length})</div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <Select value={faultBulk} onValueChange={setFaultBulk}>
                      <SelectTrigger className="w-[220px] text-white"><SelectValue placeholder={commonFaultEntries.length ? "Choose a fault" : "No common faults"} /></SelectTrigger>
                      <SelectContent className="bg-slate-900 text-white border-slate-800 max-h-60 overflow-auto">
                        <SelectItem value="__none__" disabled className="text-slate-400">— select a fault —</SelectItem>
                        {commonFaultEntries.length ? commonFaultEntries.map(([code, desc]) => (
                          <SelectItem key={code} value={code} className="text-white">{desc || code}</SelectItem>
                        )) : (
                          <SelectItem value="" disabled className="text-white">No common options</SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                    <Button disabled={!faultBulk || commonFaultEntries.length === 0} onClick={() => applyFaultToSelected(faultBulk)} className={`${btnSolid}`}>Apply</Button>
                    <Button onClick={resolveFaultSelected} className={`${btnOutlineDark}`}>Resolve</Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Node Details */}
          <Card className="bg-slate-900 border-slate-800 text-white">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-white"><Wrench className="h-5 w-5"/> Node status</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-white">
              {selectedNode ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="font-medium text-white">{selectedNode.data.label}</div>
                    <Badge className={`${statusColor(nodeStatus[selectedNode.id])} border-0`}>{humanStatus(nodeStatus[selectedNode.id])}</Badge>
                  </div>

                  {/* Metrics (from backend schema) */}
                  {(() => { const m = nodeMetrics[selectedNode.id] || {}; return (
                    <div className="grid grid-cols-2 gap-2 text-center text-xs">
                      <div className="bg-slate-800 rounded-xl p-2">
                        <div className="font-semibold text-white">{(m.traffic_speed ?? 0).toFixed(2)} Mbps</div>
                        <div className="text-white">Speed</div>
                      </div>
                      <div className="bg-slate-800 rounded-xl p-2">
                        <div className="font-semibold text-white">{m.traffic_volume ?? 0}</div>
                        <div className="text-white">Volume</div>
                      </div>
                    </div>
                  ); })()}

                  {/* Fault controls for selected node */}
                  <div className="space-y-2">
                    <div className="text-xs uppercase tracking-wide text-white/80">Node faults</div>
                    <div className="flex items-center gap-2">
                      <Select value={faultSingle} onValueChange={setFaultSingle}>
                        <SelectTrigger className="w-[220px] text-white"><SelectValue placeholder="Choose a fault" /></SelectTrigger>
                        <SelectContent className="bg-slate-900 text-white border-slate-800 max-h-60 overflow-auto">
                          <SelectItem value="__none__" disabled className="text-slate-400">— select a fault —</SelectItem>
                          {Object.entries(availableFaults[selectedNode.id] || {}).map(([code, desc]) => (
                            <SelectItem key={code} value={code} className="text-white">{desc || code}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Button disabled={!faultSingle} onClick={() => applyFaultToNode(selectedNode.id, faultSingle)} className={`${btnSolid}`}><AlertTriangle className="mr-2 h-4 w-4"/>Apply</Button>
                      <Button onClick={() => resolveFault(selectedNode.id)} className={`${btnOutlineDark}`}><CheckCircle2 className="mr-2 h-4 w-4"/>Resolve</Button>
                    </div>
                    {activeFault[selectedNode.id] && (
                      <div className="text-sm text-white/80">Active fault: <span className="text-white font-medium">{activeFault[selectedNode.id]}</span></div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="text-sm text-white">Select a node on the topology to view details and control its state.</div>
              )}
            </CardContent>
          </Card>

          {/* Agent Console with Tabs */}
          <Card className="flex-1 min-h-0 bg-slate-900 border-slate-800 text-white">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-white"><Activity className="h-5 w-5"/> Agent</CardTitle>
            </CardHeader>
            <CardContent className="h-full flex flex-col gap-3 text-white min-h-0">
              <Tabs defaultValue="logs" className="flex-1 flex flex-col min-h-0">
                <TabsList className="bg-slate-900 border border-slate-700 rounded-xl p-0.5 inline-flex gap-3">
                  <TabsTrigger
                    value="logs"
                    className="relative !outline-none focus:!outline-none focus-visible:!outline-none text-slate-200 hover:text-white border border-transparent data-[state=active]:bg-sky-600 data-[state=active]:text-white data-[state=active]:border-sky-400 data-[state=active]:shadow data-[state=active]:shadow-sky-900 data-[state=active]:font-semibold rounded-lg px-4 py-1.5 transition
                    data-[state=active]:after:content-[''] data-[state=active]:after:absolute data-[state=active]:after:left-2 data-[state=active]:after:right-2 data-[state=active]:after:-bottom-1 data-[state=active]:after:h-0.5 data-[state=active]:after:bg-sky-300 data-[state=active]:after:rounded-full">
                    Logs
                  </TabsTrigger>
                  <TabsTrigger
                    value="agent"
                    className="relative !outline-none focus:!outline-none focus-visible:!outline-none text-slate-200 hover:text-white border border-transparent data-[state=active]:bg-sky-600 data-[state=active]:text-white data-[state=active]:border-sky-400 data-[state=active]:shadow data-[state=active]:shadow-sky-900 data-[state=active]:font-semibold rounded-lg px-4 py-1.5 transition
                    data-[state=active]:after:content-[''] data-[state=active]:after:absolute data-[state=active]:after:left-2 data-[state=active]:after:right-2 data-[state=active]:after:-bottom-1 data-[state=active]:after:h-0.5 data-[state=active]:after:bg-sky-300 data-[state=active]:after:rounded-full">
                    Controls & Reasoning
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="logs" className="flex-1 min-h-0 flex flex-col">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="opacity-80">Show:</span>
                      <Select value={String(logLimit)} onValueChange={(v) => setLogLimit(Number(v))}>
                        <SelectTrigger className="w-[100px] text-white"><SelectValue /></SelectTrigger>
                        <SelectContent className="bg-slate-900 text-white border-slate-800">
                          <SelectItem value="50">50</SelectItem>
                          <SelectItem value="100">100</SelectItem>
                          <SelectItem value="200">200</SelectItem>
                          <SelectItem value="500">500</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <Button variant="ghost" className="text-white hover:bg-slate-800 hover:text-white focus:!outline-none focus:!ring-0 focus-visible:!outline-none focus-visible:!ring-0" onClick={() => selectedNodeId ? setAgentLogs(prev => prev.filter(l => l.scope !== selectedNodeId)) : setAgentLogs([])}><Trash2 className="mr-2 h-4 w-4"/>Clear logs</Button>
                  </div>

                  <div className="rounded-2xl border border-slate-800 bg-slate-900 flex-1 min-h-0 overflow-auto p-2 space-y-2">
                    {filteredLogs.length === 0 ? (
                      <div className="text-sm text-white p-2">No messages yet. Run diagnostics or wait for status updates…</div>
                    ) : (
                      filteredLogs.slice(0, logLimit).map((l, idx) => (
                        <div key={idx} className="text-sm p-2 rounded-xl bg-slate-800 border border-slate-700 flex items-start gap-2 text-white">
                          <div className={`mt-1 h-2.5 w-2.5 rounded-full ${dotColor(l.status)}`} />
                          <div className="flex-1 space-y-1">
                            <div className="text-[11px] text-white flex items-center gap-2 flex-wrap">
                              <span className="font-mono">{new Date(l.ts).toLocaleTimeString()}</span>
                              <span className="opacity-70">node:</span>
                              <span className="font-mono">{l.scope}</span>
                              {l.node_type && (<><span className="opacity-70">type:</span><span className="font-mono">{l.node_type}</span></>)}
                              <span className="opacity-70">status:</span>
                              <span className="font-mono">{humanStatus(l.status)}</span>
                            </div>
                            <div className="flex items-center gap-3 text-xs opacity-90 flex-wrap">
                              {typeof l.traffic_speed !== 'undefined' && <span className="px-2 py-0.5 rounded bg-slate-700 border border-slate-600">speed: {Number(l.traffic_speed).toFixed(2)} Mbps</span>}
                              {typeof l.traffic_volume !== 'undefined' && <span className="px-2 py-0.5 rounded bg-slate-700 border border-slate-600">vol: {l.traffic_volume}</span>}
                            </div>
                            {l.error && <div className="text-white/90">⚠ {l.error}</div>}
                            {l.reasoning && <div className="text-white/90">{l.reasoning}</div>}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </TabsContent>

                <TabsContent value="agent" className="flex-1 min-h-0 flex flex-col">
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-3">
                      {selectedNodeId && (
                        <Button
                          aria-pressed={agentRunning}
                          data-state={agentRunning ? 'on' : 'off'}
                          onClick={toggleSimulate}
                          className={`${agentRunning ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500 hover:bg-emerald-500/25' : 'bg-rose-500/15 text-rose-300 border border-rose-500 hover:bg-rose-500/25'} px-3 py-1.5 rounded-lg transition-colors shadow font-semibold focus:!outline-none focus:!ring-0 focus-visible:!outline-none focus-visible:!ring-0`}
                        >
                          {agentRunning ? 'Running' : 'Paused'}
                        </Button>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <Button className={btnOutlineDark} onClick={runDiagnostic}><Play className="mr-2 h-4 w-4"/>Diagnostics</Button>
                      <Button variant="ghost" className={btnGhostDark} onClick={() => selectedNodeId ? setAgentLogs(prev => prev.filter(l => l.scope !== selectedNodeId)) : setAgentReasoning("") }><Trash2 className="mr-2 h-4 w-4"/>Clear reasoning</Button>
                    </div>
                  </div>

                  <div className="mt-3 flex-1 min-h-0 flex flex-col">
                    <div className="text-xs uppercase tracking-wide text-white/80">Latest reasoning</div>
                    <pre className="rounded-2xl border border-slate-800 bg-slate-950 p-3 flex-1 min-h-0 overflow-auto whitespace-pre-wrap text-sm">{reasoningView}</pre>
                  </div>
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
