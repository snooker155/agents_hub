/**
 * Client-side mirror of tools/capabilities.py.
 *
 * The server is authoritative — it refuses the save with a 409 carrying the
 * structured violation. This exists so the agent editor can show the problem
 * *while* tools are being toggled instead of only after a failed save, which is
 * the difference between a guard people design around and one they resent.
 *
 * Capabilities themselves are not duplicated here: each tool's grants arrive
 * from `GET /api/tools` (`tool.capabilities`). Only the rules live in this file.
 */

export const CAPABILITY_LABELS = {
  ingests_untrusted: 'ingests untrusted content',
  reads_private: 'reads private data',
  can_exfiltrate: 'can send data outside',
};

export const CAPABILITY_ORDER = ['ingests_untrusted', 'reads_private', 'can_exfiltrate'];

// Mirrors BLOCKED_COMBINATIONS. `reads_private + can_exfiltrate` and
// `reads_private + ingests_untrusted` stay legal on purpose — neither closes
// the loop on its own, and blocking them would break every code agent.
export const BLOCKED_COMBINATIONS = [
  {
    id: 'lethal_trifecta',
    capabilities: ['ingests_untrusted', 'reads_private', 'can_exfiltrate'],
    title: 'Lethal trifecta',
    explanation:
      'This agent can ingest attacker-controllable text, read private data and send data outside. '
      + 'Text it reads can instruct it to collect secrets and forward them, with nothing in the loop to stop it.',
    severity: 'block',
  },
  {
    id: 'exfiltration_path',
    capabilities: ['ingests_untrusted', 'can_exfiltrate'],
    title: 'Exfiltration path',
    explanation:
      "This agent can ingest attacker-controllable text and send data outside. Injected instructions "
      + "have a direct outbound channel — anything already in the agent's context can leave through it. "
      + 'Grant it only when the outbound reach is the point.',
    severity: 'warn',
  },
];

/** capability -> [tool ids that granted it], for the selected tools. */
export function capabilitySources(selectedTools, toolsMeta) {
  const sources = {};
  (selectedTools || []).forEach((tid) => {
    const caps = toolsMeta?.[tid]?.capabilities || [];
    caps.forEach((cap) => {
      if (!sources[cap]) sources[cap] = [];
      if (!sources[cap].includes(tid)) sources[cap].push(tid);
    });
  });
  return sources;
}

/**
 * First blocked combination the selection forms, or null.
 * Returns { id, title, explanation, capabilities, sources, message }.
 */
export function checkCombination(selectedTools, toolsMeta) {
  const sources = capabilitySources(selectedTools, toolsMeta);
  const held = new Set(Object.keys(sources));
  const matched = BLOCKED_COMBINATIONS.filter((r) => r.capabilities.every((c) => held.has(c)));
  if (!matched.length) return null;
  // A blocking rule always wins over a warning one, so a tool set forming both
  // the trifecta and the pair reports the trifecta.
  const rule = matched.find((r) => r.severity === 'block') || matched[0];

  const ruleSources = {};
  rule.capabilities.forEach((c) => { ruleSources[c] = sources[c] || []; });
  const message = CAPABILITY_ORDER
    .filter((c) => rule.capabilities.includes(c))
    .map((c) => `${CAPABILITY_LABELS[c]} (${(ruleSources[c] || []).join(', ') || '?'})`)
    .join(' + ');

  return { ...rule, sources: ruleSources, message, blocking: rule.severity === 'block' };
}
