/**
 * The Assistant page in the demo (docs/assistant.md): a thread with two
 * recorded turns, and one fixed reply to anything new. There are no speech
 * models behind the demo, so the page uses the browser's own voice and
 * recognition, the same fallback a hub without them gets.
 */

const TURNS = [
  {
    user: 'What is new for me today?',
    tools: ['list_notifications', 'list_tasks'],
    answer: [
      'Two tasks wait for you and one run finished overnight.',
      '',
      '- [Draft the weekly report](/tasks) is due today',
      '- [Review the support flow](/flows) waits for your approval',
      '- The nightly research run finished, see [Messages](/messages)',
    ].join('\n'),
  },
  {
    user: 'How much did I spend this month?',
    tools: ['costs_summary'],
    answer: [
      'You spent 4 dollars 20 of your 50 dollar limit this month.',
      '',
      'Most of it went to the research team. The breakdown is on [Costs](/costs).',
    ].join('\n'),
  },
];

export const ASSISTANT_DEMO_THREAD = {
  messages: TURNS.flatMap((t) => [
    { role: 'user', content: t.user },
    { role: 'assistant', content: t.answer },
  ]),
  trace: TURNS.flatMap((t) => [
    { k: 'user', text: t.user },
    ...t.tools.map((tool) => ({ k: 'tool', tool, status: 'done' })),
    { k: 'assistant', text: t.answer },
  ]),
  chat_ref: null,
  agent_id: 'assistant',
  mode: 'personal',
  home: 'demo',
  workspaces: ['demo'],
  service_available: false,
  voice: { transcription: null, speech: null, max_seconds: 120, max_bytes: 8388608, max_speak_chars: 1000 },
};

export const ASSISTANT_DEMO_TEXT = [
  'This is the public demo, so the assistant answers with this one fixed reply.',
  'In a real install it works in your workspaces, by voice or by text.',
  '',
  'Open [Tasks](/tasks) or [Costs](/costs) to see a page shown beside the conversation.',
].join('\n');

// The steps a turn takes before the fixed reply, each as long as a real one,
// so the live mark plays the scene a real assistant would show for it.
const DEMO_STEPS = [
  ['think', { thought: 'What changed for this person since yesterday?' }],
  ['hub_lookup', { kind: 'notification' }],
  ['hub_lookup', { kind: 'run', workspace: 'all' }],
  ['search_docs', { query: 'assistant' }],
  ['costs_summary', {}],
];

export const ASSISTANT_DEMO_FRAMES = [
  { event: 'run', data: { run_id: 'demo-assistant' } },
  ...DEMO_STEPS.flatMap(([tool, input]) => [
    { event: 'tool_start', data: { tool, input } },
    { event: 'tool_end', data: { tool, demo_pause: 2600 } },
  ]),
  ...ASSISTANT_DEMO_TEXT.split(/(?<= )/).map((token) => ({ event: 'token', data: { token } })),
  { event: 'message', data: { role: 'assistant', content: ASSISTANT_DEMO_TEXT } },
  { event: 'done', data: { ok: true, response: ASSISTANT_DEMO_TEXT } },
];

/** What the voice routes answer in the demo: no model, so the browser's own. */
export const ASSISTANT_DEMO_NO_MODEL = {
  detail: { code: 'model_not_added', message: 'The demo has no speech models.' },
};
