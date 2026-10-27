/**
 * The tool calls a background flow run or a team run waits on
 * (common/tool_approvals.py, docs/hooks.md "In a background flow, loop or
 * team"): one card per call, with Approve and Deny, on every page that shows
 * the run (the flow run, the loop's running pass, the team, the chat that
 * started the run). Give it `flowRunId` or `teamRunId`.
 *
 * A node's wait can last hours, longer than the run channel's replay keeps
 * its events, so the cards come from the server: read when the page opens,
 * read again when the run's channel says a call started or stopped waiting.
 */
import { useCallback, useEffect, useState } from 'react';
import { useI18n } from '../../i18n';
import { listFlowRunApprovals, listTeamRunApprovals } from '../../api/toolApprovals';
import { ToolApprovalCard } from '../chat/ToolApprovalCard';
import { useRunChannels } from './liveTurns';

const EVENTS = new Set(['tool_approval', 'tool_approval_resolved']);

export default function RunApprovals({ flowRunId = null, teamRunId = null, className = '' }) {
  const { t } = useI18n();
  const runKey = teamRunId ? `team:${teamRunId}` : (flowRunId ? `run:${flowRunId}` : null);
  // Kept with the run they were read for: another run starts empty.
  const [state, setState] = useState({ key: null, rows: [] });
  const rows = state.key === runKey ? state.rows : [];
  const setRows = useCallback((fn) => {
    setState((prev) => ({ key: runKey, rows: fn(prev.key === runKey ? prev.rows : []) }));
  }, [runKey]);
  // Who asked, from the channel's events.
  const [labels, setLabels] = useState({});

  const load = useCallback(() => {
    if (!runKey) return undefined;
    let current = true;
    const read = teamRunId ? listTeamRunApprovals(teamRunId, 'pending') : listFlowRunApprovals(flowRunId, 'pending');
    read
      .then(({ data }) => { if (current) setRows(() => data?.approvals || []); })
      .catch(() => {});
    return () => { current = false; };
  }, [runKey, flowRunId, teamRunId, setRows]);

  useEffect(() => load(), [load]);

  // The channel the run streams on: a flow run's `run:<id>`, a team's `team:<id>`.
  useRunChannels(runKey ? [runKey] : [], (event) => {
    if (!EVENTS.has(event?.type)) return;
    // A flow node's events carry its label, a team turn's its speaker.
    const who = event.agent_label || event.speaker;
    if (event.approval_id && who) {
      setLabels((prev) => ({ ...prev, [event.approval_id]: who }));
    }
    if (event.type === 'tool_approval_resolved') {
      setRows((prev) => prev.filter((r) => r.approval_id !== event.approval_id));
      return;
    }
    load();
  });

  if (!rows.length) return null;
  return (
    <div className={`space-y-1.5 ${className}`} data-testid="run-approvals">
      {rows.map((row) => (
        <div key={row.approval_id}>
          <p className="mb-0.5 text-[11px] text-amber-700">
            {t('toolApproval.inRun', { agent: labels[row.approval_id] || row.agent_id || '' })}
          </p>
          <ToolApprovalCard
            approval={row}
            onAnswered={() => setRows((prev) => prev.filter((r) => r.approval_id !== row.approval_id))}
          />
        </div>
      ))}
    </div>
  );
}
