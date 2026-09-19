export default {
  setupProgress: 'Setup progress: {{done}} / {{total}}',
  reCheck: 'Re-check',
  backend: {
    title: 'Backend is running',
    done: 'The API is reachable.',
    todo: 'Start the FastAPI backend, then re-check. See the Install section below.',
    unreachable: 'Can\'t reach the API. Start the FastAPI backend (see the Install section below), then press Re-check.',
  },
  provider: {
    title: 'Connect an LLM provider',
    done: 'At least one provider is configured.',
    doneWithLabel: 'At least one provider is configured (default: {{provider}}).',
    warn: 'A provider is configured but the connection test failed — check the API key / base URL in Settings.',
    todo: 'Add an API key (OpenAI / Anthropic / Google) or point at a local model (Ollama / LM Studio) in Settings.',
    openSettings: 'Open Settings',
    testConnection: 'Test connection',
  },
  workspace: {
    title: 'Create a workspace',
    done: 'You have {{count}} workspaces. Workspaces isolate tasks, files and memory.',
    todo: 'A workspace is the boundary for your tasks, projects, files and memory pools.',
    manage: 'Manage workspaces',
  },
  agent: {
    title: 'Add an agent (optional)',
    done: '{{count}} agents available.',
    todo: 'Built-in agents work out of the box. Browse the Marketplace to clone specialised roles into your workspace.',
    openMarketplace: 'Open Marketplace',
  },
  chat: {
    title: 'Start your first chat',
    desc: 'Ask an agent to do something — e.g. "Summarise the files in this workspace" or "Create a task to add a README".',
    openChat: 'Open Chat',
  },
};
