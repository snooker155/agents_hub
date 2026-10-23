import { describe, expect, it } from 'vitest';
import { entryAgent, gradedAgent } from '../task/taskUtils';

const runs = [
  { agent_id: 'decomposer', started_at: '2026-10-21T00:49:53.430Z', finished_at: '2026-10-21T00:50:05.000Z' },
  { agent_id: 'swe_agent', started_at: '2026-10-21T02:50:03.610Z', finished_at: '2026-10-21T02:50:14.000Z' },
  { agent_id: 'outcome_grader', started_at: '2026-10-21T02:50:16.441Z', finished_at: '2026-10-21T02:50:16.441Z' },
];

describe('entryAgent', () => {
  it('prefers the agent the entry names', () => {
    expect(entryAgent({ agent_id: 'x', timestamp: '2026-10-21T02:50:05Z' }, runs)).toBe('x');
  });

  it('finds the run going on at the time, its launch a little before its start', () => {
    expect(entryAgent({ type: 'status_change', timestamp: '2026-10-21T02:49:52.800Z' }, runs)).toBe('swe_agent');
    expect(entryAgent({ type: 'status_change', timestamp: '2026-10-21T02:50:16.445Z' }, runs)).toBe('swe_agent');
  });

  it('never puts an agent on the creation', () => {
    expect(entryAgent({ type: 'created', timestamp: '2026-10-21T00:49:53.382Z' }, runs)).toBeNull();
  });

  it('puts a grading on the grader and keeps whose work it graded', () => {
    const old = { type: 'outcome_graded', agent_id: 'swe_agent', timestamp: '2026-10-21T02:50:16.445Z' };
    expect(entryAgent(old, runs)).toBe('outcome_grader');
    expect(gradedAgent(old)).toBe('swe_agent');
    const fresh = { type: 'outcome_graded', agent: 'outcome_grader', graded_agent: 'swe_agent' };
    expect(entryAgent(fresh, runs)).toBe('outcome_grader');
    expect(gradedAgent(fresh)).toBe('swe_agent');
  });
});
