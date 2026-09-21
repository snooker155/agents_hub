/**
 * The code someone pastes into their own project to start reporting.
 *
 * Kept out of the page component so it can be tested as what it is: generated
 * source that has to be correct on the first paste. A snippet that needs
 * editing before it runs is a snippet that turns a two-minute setup into a
 * support conversation.
 *
 * The token is interpolated only when it is in hand, which is the moment right
 * after a connection is created. Everywhere else the placeholder stands in,
 * because the hub cannot show a token it deliberately did not keep.
 *
 * The install line names the path in this repository rather than a registry: the
 * clients are not published yet, and a snippet whose first line fails is worse
 * than no snippet at all.
 */

const TOKEN_PLACEHOLDER = 'ahc_…';

/** Fall back to the page's own origin: the hub is usually reached at the same
 *  address the person is looking at it on, and a wrong URL is the most common
 *  reason a first report never arrives. */
function hubUrl(explicit) {
  if (explicit) return explicit;
  if (typeof window !== 'undefined' && window.location) return window.location.origin;
  return 'http://localhost:8000';
}

export function pythonSnippet({ token, url, connectionId } = {}) {
  return [
    '# pip install -e clients/agents-hub-langgraph   (from the hub repository)',
    'from agents_hub_langgraph import HubTracer',
    '',
    `tracer = HubTracer(url="${hubUrl(url)}", token="${token || TOKEN_PLACEHOLDER}")`,
    'tracer.report_graph(graph)          # once, so the hub can draw it',
    '',
    'graph.invoke(state, config={"callbacks": [tracer]})',
    ...(connectionId ? [`# reports as: ${connectionId}`] : []),
  ].join('\n');
}

export function jsSnippet({ token, url, connectionId } = {}) {
  return [
    '// npm install ./clients/agents-hub-langgraph-js   (from the hub repository)',
    'import { HubTracer } from "agents-hub-langgraph";',
    '',
    `const tracer = new HubTracer({ url: "${hubUrl(url)}", token: "${token || TOKEN_PLACEHOLDER}", graph });`,
    'await tracer.reportGraph();         // once, so the hub can draw it',
    '',
    'await graph.invoke(state, { callbacks: [tracer] });',
    ...(connectionId ? [`// reports as: ${connectionId}`] : []),
  ].join('\n');
}

/** For a team that already exports traces: no library and no code, two
 *  variables on the process they already run. Written as a *second* exporter on
 *  purpose — nobody trades the backend they have for one they are evaluating. */
export function otelSnippet({ token, url } = {}) {
  return [
    '# already exporting spans? add this hub as a second destination.',
    `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=${hubUrl(url)}/api/ingest/v1/traces`,
    `OTEL_EXPORTER_OTLP_TRACES_HEADERS=x-connection-token=${token || TOKEN_PLACEHOLDER}`,
    '',
    '# protobuf or JSON, whichever your exporter sends. Spans arrive when they',
    '# end, so a run appears here once its trace finishes.',
  ].join('\n');
}

export function curlSnippet({ token, url } = {}) {
  const base = hubUrl(url);
  const auth = `-H 'authorization: Bearer ${token || TOKEN_PLACEHOLDER}'`;
  return [
    `curl -X POST ${base}/api/ingest/runs \\`,
    `     ${auth} \\`,
    "     -H 'content-type: application/json' \\",
    `     -d '{"input": "hello"}'`,
    '',
    '# -> {"run_id": "ing-…", "session_id": "…"}',
    '# then POST /api/ingest/runs/<run_id>/events and /close',
  ].join('\n');
}

export function envSnippet({ token, url } = {}) {
  return [
    `AGENTS_HUB_URL=${hubUrl(url)}`,
    `AGENTS_HUB_TOKEN=${token || TOKEN_PLACEHOLDER}`,
  ].join('\n');
}

export const SNIPPETS = {
  python: { label: 'LangGraph', build: pythonSnippet, language: 'python' },
  js: { label: 'LangGraph.js', build: jsSnippet, language: 'javascript' },
  otel: { label: 'OpenTelemetry', build: otelSnippet, language: 'bash' },
  env: { label: 'Environment', build: envSnippet, language: 'bash' },
  curl: { label: 'HTTP', build: curlSnippet, language: 'bash' },
};

export { TOKEN_PLACEHOLDER };
