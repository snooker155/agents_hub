// The tool capability model (tools/capabilities.py): capability names, the
// blocked combinations and the delegation suffix, as the agent editor and
// the MCP page show them. The backend text stays the fallback for a rule
// this file does not know.
export default {
  labels: {
    ingests_untrusted: 'ingests untrusted content',
    reads_private: 'reads private data',
    can_exfiltrate: 'can send data outside',
  },
  via: 'via',
  rules: {
    lethal_trifecta: {
      title: 'Lethal trifecta',
      explanation: 'This agent can ingest attacker-controllable text, read private data and send data outside. Text it reads can instruct it to collect secrets and forward them, with nothing in the loop to stop it.',
    },
    exfiltration_path: {
      title: 'Exfiltration path',
      explanation: 'This agent can ingest attacker-controllable text and send data outside. Injected instructions have a direct outbound channel: anything already in the agent\'s context can leave through it. Grant it only when the outbound reach is the point.',
    },
    system_workspace: {
      title: 'System workspace rule',
      explanation: 'An agent of the system workspace may never hold a push, shell, delegation or outbound tool.',
    },
  },
  viaDelegation: {
    title: 'via delegation',
    explanation: 'This combination only closes through agents this one can delegate to; restrict its delegates list to remove the path.',
  },
};
