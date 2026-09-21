import { describe, it, expect } from 'vitest';
import { SNIPPETS, TOKEN_PLACEHOLDER, curlSnippet, envSnippet, jsSnippet, otelSnippet, pythonSnippet } from '../connectionSnippet';

// Generated source someone pastes into their own project. It has to run on the
// first paste: a snippet that needs editing first is the difference between a
// two-minute setup and a support conversation.

describe('connection snippets', () => {
  it('carries the token when it is in hand', () => {
    const code = pythonSnippet({ token: 'ahc_abc', connectionId: 'billing' });
    expect(code).toContain('token="ahc_abc"');
    expect(code).toContain('HubTracer');
    expect(code).toContain('report_graph');
  });

  it('shows a placeholder when the token is gone, because it cannot be recovered', () => {
    expect(pythonSnippet({})).toContain(TOKEN_PLACEHOLDER);
    expect(curlSnippet({})).toContain(TOKEN_PLACEHOLDER);
    expect(envSnippet({})).toContain(TOKEN_PLACEHOLDER);
  });

  it('defaults to the address the person is already looking at', () => {
    // A wrong URL is the most common reason a first report never arrives.
    expect(pythonSnippet({})).toContain(window.location.origin);
  });

  it('honours an explicit hub url', () => {
    expect(curlSnippet({ url: 'https://hub.internal' }))
      .toContain('https://hub.internal/api/ingest/runs');
  });

  it('points an existing exporter at this hub without any code', () => {
    // The whole OpenTelemetry integration is two variables, so the snippet has
    // to carry the exact endpoint path and header name.
    const code = otelSnippet({ token: 'ahc_abc', url: 'https://hub.internal' });
    expect(code).toContain('https://hub.internal/api/ingest/v1/traces');
    expect(code).toContain('x-connection-token=ahc_abc');
    expect(code).toContain('second destination');
  });

  it('spells the JavaScript client the way its package does', () => {
    const code = jsSnippet({ token: 'ahc_abc' });
    expect(code).toContain('agents-hub-langgraph');
    expect(code).toContain('new HubTracer({');
    expect(code).toContain('reportGraph');
  });

  it('offers one snippet per way of reporting', () => {
    expect(Object.keys(SNIPPETS)).toEqual(['python', 'js', 'otel', 'env', 'curl']);
    Object.values(SNIPPETS).forEach(({ build, label }) => {
      expect(label).toBeTruthy();
      expect(build({ token: 'ahc_x' })).toContain('ahc_x');
    });
  });
});
