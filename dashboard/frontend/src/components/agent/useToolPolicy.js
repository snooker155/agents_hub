import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAgentToolPolicy, getToolPolicyDecisions, updateAgentToolPolicy } from '../../api/toolPolicy';

export const TOOL_POLICY_MODES = ['always_allow', 'always_ask', 'auto'];
export const TOOL_POLICY_INHERIT = '';

const sameMap = (a, b) => {
  const ka = Object.keys(a || {});
  const kb = Object.keys(b || {});
  return ka.length === kb.length && ka.every((k) => a[k] === b[k]);
};

/**
 * The agent's per-tool permission policy (`AgentSpec.tool_policy`, see
 * tools/permission_policy.py): a default for every tool ("*") and a mode per
 * tool on the agent's record, each one inheriting when left empty.
 *
 * The Tools tab shows the default in the card header and each tool's mode
 * inside the tool's own card, so the policy is edited where the tool is
 * turned on. The effective mode next to a tool comes from the backend, which
 * resolves agent entries, then the workspace policy, then the approval list.
 */
export default function useToolPolicy({ agentId, workspace, onSaved, loadFailedText, saveFailedText }) {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [decisions, setDecisions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  // Read through refs so new text (a language switch) does not refetch.
  const textRef = useRef({ loadFailedText, saveFailedText });
  textRef.current = { loadFailedText, saveFailedText };

  const load = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    setError('');
    try {
      const { data: body } = await getAgentToolPolicy(agentId, workspace);
      setData(body);
      setDraft(body?.tool_policy || {});
    } catch (e) {
      setError(e?.response?.data?.detail || textRef.current.loadFailedText);
    } finally {
      setLoading(false);
    }
    try {
      const { data: rows } = await getToolPolicyDecisions({ agentId, limit: 10 });
      setDecisions(Array.isArray(rows?.decisions) ? rows.decisions : []);
    } catch {
      setDecisions([]);
    }
  }, [agentId, workspace]);

  useEffect(() => { load(); }, [load]);

  const dirty = useMemo(() => !sameMap(draft, data?.tool_policy || {}), [draft, data]);
  const effective = useMemo(
    () => Object.fromEntries((data?.effective || []).map((row) => [row.tool, row])),
    [data],
  );
  // Tools an ``mcp:<server>`` group on the record stands for (the backend
  // expands it from the server's last connect): shown with their group.
  const groupOf = useMemo(() => {
    const out = {};
    for (const [alias, ids] of Object.entries(data?.groups || {})) {
      for (const id of ids || []) out[id] = alias;
    }
    return out;
  }, [data]);

  const setMode = useCallback((tool, mode) => {
    setSaved(false);
    setDraft((prev) => {
      const next = { ...prev };
      if (mode === TOOL_POLICY_INHERIT) delete next[tool];
      else next[tool] = mode;
      return next;
    });
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    setSaved(false);
    setError('');
    try {
      const { data: body } = await updateAgentToolPolicy(agentId, draft, workspace);
      setData(body);
      setDraft(body?.tool_policy || {});
      setSaved(true);
      if (onSaved) onSaved();
      return true;
    } catch (e) {
      setError(e?.response?.data?.detail || textRef.current.saveFailedText);
      return false;
    } finally {
      setSaving(false);
    }
  }, [agentId, draft, workspace, onSaved]);

  return {
    data, draft, decisions, loading, saving, error, saved, dirty,
    effective, groupOf, setMode, save, reload: load,
    modes: Array.isArray(data?.modes) && data.modes.length ? data.modes : TOOL_POLICY_MODES,
  };
}
