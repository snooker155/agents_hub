import axios from 'axios';
import { describe, it, expect, beforeEach } from 'vitest';
import {
  agentRevision, forgetAgentRevisions, installAgentRevision, parseAgentUrl, setAgentConflictHandler,
} from '../agentRevision';

// An axios instance whose adapter answers from a script, recording what it was sent.
function scripted(answer) {
  const sent = [];
  const api = axios.create({
    baseURL: '/api',
    adapter: async (config) => {
      sent.push(config);
      const { status = 200, data = {}, headers = {} } = answer(config, sent.length) || {};
      const response = { data, status, statusText: String(status), headers, config };
      if (status >= 400) {
        const error = new Error(`status ${status}`);
        error.response = response;
        error.config = config;
        throw error;
      }
      return response;
    },
  });
  installAgentRevision(api);
  return { api, sent };
}

const ifMatch = (config) => {
  const h = config.headers || {};
  return typeof h.get === 'function' ? h.get('If-Match') : h['If-Match'];
};

describe('agentRevision', () => {
  beforeEach(() => {
    forgetAgentRevisions();
    setAgentConflictHandler(null);
  });

  it('parses agent urls', () => {
    expect(parseAgentUrl('/agents/a1/definition?x=1')).toEqual({ id: 'a1', sub: 'definition' });
    expect(parseAgentUrl('/agents/a1')).toEqual({ id: 'a1', sub: '' });
    expect(parseAgentUrl('/agents/tools')).toBeNull();
    expect(parseAgentUrl('/tasks/1')).toBeNull();
  });

  it('remembers the tag of the agent read and sends it on edits only', async () => {
    const { api, sent } = scripted(() => ({ headers: { etag: '"h1"' } }));
    await api.get('/agents/a1');
    expect(agentRevision('a1')).toBe('h1');
    await api.put('/agents/a1/definition', { instructions: 'x' });
    expect(ifMatch(sent[1])).toBe('"h1"');
    await api.post('/agents/a1/proactive/wake');
    expect(ifMatch(sent[2])).toBeFalsy();
  });

  it('takes the new tag from a write, not from other reads', async () => {
    let tag = '"h1"';
    const { api } = scripted((config) => ({ headers: { etag: config.method === 'get' && config.url.endsWith('/logs') ? '"other"' : tag } }));
    await api.get('/agents/a1');
    tag = '"h2"';
    await api.post('/agents/a1/tools', { tools: [] });
    expect(agentRevision('a1')).toBe('h2');
    await api.get('/agents/a1/logs');
    expect(agentRevision('a1')).toBe('h2');
  });

  it('overwrites on a conflict when the handler says so', async () => {
    const conflict = { status: 409, data: { error: 'version_conflict', current_hash: 'h9', current_version: 4, detail: 'changed' } };
    const { api, sent } = scripted((config, n) => (n === 2 ? conflict : { headers: { etag: n === 1 ? '"h1"' : '"h10"' } }));
    const asked = [];
    setAgentConflictHandler(async (info) => { asked.push(info); return 'overwrite'; });
    await api.get('/agents/a1');
    const res = await api.put('/agents/a1/definition', { instructions: 'x' });
    expect(res.status).toBe(200);
    expect(asked[0]).toMatchObject({ agentId: 'a1', currentVersion: 4, currentHash: 'h9' });
    expect(ifMatch(sent[2])).toBe('"h9"');
    expect(agentRevision('a1')).toBe('h10');
  });

  it('rejects the conflict when nobody handles it or the user reloads', async () => {
    const conflict = { status: 409, data: { error: 'version_conflict', current_hash: 'h9' } };
    const { api } = scripted((config) => (config.method === 'get' ? { headers: { etag: '"h1"' } } : conflict));
    await api.get('/agents/a1');
    await expect(api.put('/agents/a1/definition', {})).rejects.toMatchObject({ response: { status: 409 } });
    setAgentConflictHandler(async () => 'cancel');
    await expect(api.put('/agents/a1/definition', {})).rejects.toMatchObject({ response: { status: 409 } });
  });
});
