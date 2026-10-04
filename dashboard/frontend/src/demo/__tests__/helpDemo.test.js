import { describe, expect, it } from 'vitest';
import { createHandlers, HELP_DEMO_TEXT } from '../handlers';
import { parseHelpText } from '../../components/help/helpLinks';

/** The Help panel in the demo: no model, one fixed answer that still links. */
describe('demo Help', () => {
  const generic = () => createHandlers({ fixtures: {}, streams: {} })[1];

  it('loads an empty conversation for the support agent', async () => {
    const request = new Request('http://demo.local/api/help-chat');
    const body = await (await generic().run({ request, requestId: '1' })).response.json();
    expect(body).toMatchObject({ messages: [], agent_id: 'support' });
  });

  it('streams the fixed reply for a question', async () => {
    const request = new Request('http://demo.local/api/help-chat', {
      method: 'POST', body: JSON.stringify({ message: 'help' }),
      headers: { 'Content-Type': 'application/json' },
    });
    const { response } = await generic().run({ request, requestId: '2' });
    expect(response.headers.get('Content-Type')).toBe('text/event-stream');
    const text = await response.text();
    expect(text).toContain('"type":"token"');
    expect(text).toContain('"type":"done"');
  });

  it('answers with links the panel turns into navigation and the tour', () => {
    const kinds = parseHelpText(HELP_DEMO_TEXT).map((s) => s.type);
    expect(kinds).toContain('nav');
    expect(kinds).toContain('tour');
  });
});
