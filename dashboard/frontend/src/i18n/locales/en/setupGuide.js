/**
 * The guided setup (common/setup_guide.py, components/setup): the panel's
 * own chrome, and translated copy for each step id. The server's own
 * `title`/`why` are the fallback (read through `t(..., { defaultValue })`),
 * so a step this file has not labelled yet still shows something.
 */
export default {
  pill: 'Setup {{done}}/{{total}}',
  progress: '{{done}} of {{total}}',
  groups: {
    setup: 'Set up the hub',
    start: 'Start using it',
  },
  loading: 'Loading the guide…',
  doIt: 'Do it with the assistant',
  skip: 'Skip',
  unskip: 'Undo skip',
  takeTour: 'Take the tour',
  restart: 'Restart guide',
  finish: 'Finish',
  steps: {
    model: {
      title: 'Connect a model',
      why: 'Every agent, you included, thinks with a model, so nothing runs without one.',
    },
    default_model: {
      title: 'Choose the default model',
      why: 'Every workspace without a model of its own uses this one, and it decides speed and cost.',
    },
    voice: {
      title: 'Give the assistant a voice',
      why: 'With a speech and a transcription model the assistant hears and speaks in every browser.',
    },
    web_search: {
      title: 'Turn on web search',
      why: 'Agents can then search the web, not only open pages they are given.',
    },
    demo: {
      title: 'Look around the demo workspace',
      why: 'Four agents with chats, views and a pulse show what the hub does before anything is built.',
    },
    people: {
      title: 'Invite your team',
      why: 'Each person gets their own sign in, personal workspace and assistant.',
    },
    health: {
      title: "Check the hub's health",
      why: 'The doctor checks the database, the model connection, the runtime and the rest, each with its fix.',
    },
    first_chat: {
      title: 'Talk to an agent in Chat',
      why: 'Chat is where you work with any agent, with files, references and approvals.',
    },
    channel: {
      title: 'Reach the hub from Telegram, Slack or mail',
      why: "Then you can talk to your agents from your phone's messenger or by email.",
    },
    accounts: {
      title: 'Connect your accounts',
      why: 'Agents can then read and write your Google, Microsoft, Jira, Linear or Notion data.',
    },
    first_agent: {
      title: 'Make an agent of your own',
      why: 'An agent with its own instructions, tools and model does one kind of job well.',
    },
    first_task: {
      title: 'Hand an agent a task',
      why: 'A task runs on its own, with its result, cost and history kept on the Tasks page.',
    },
    automation: {
      title: 'Let something run on its own',
      why: 'A watcher wakes an agent on new mail or a changed page; a pulse lets an agent check in on a schedule.',
    },
    tour: {
      title: 'Take the welcome tour',
      why: 'Two minutes over the main pages, so you know where everything lives.',
    },
  },
  firstModel: {
    provider: 'Provider',
    apiKey: 'API key',
    baseUrl: 'Base URL (optional)',
    connect: 'Connect',
    connecting: 'Connecting…',
    adminOnly: 'Ask your administrator to connect a model.',
    runtimeHint: "The hub's own runtime, a model downloaded onto this machine, is set on the Models page, Local tab.",
    runtimeLink: 'Open the Local tab',
    providers: {
      openai: 'OpenAI',
      anthropic: 'Anthropic',
      google: 'Google Gemini',
      ollama: 'Ollama on this machine',
      lmstudio: 'LM Studio on this machine',
    },
    errors: {
      forbidden: 'Ask your administrator to connect a model.',
      rejected: 'That key was not accepted. Check it and try again.',
      missing: 'Enter an API key first.',
      no_models: 'That provider has no model to use yet.',
      unknown_provider: 'That provider is not recognised.',
      failed: 'Could not connect. Try again.',
    },
  },
};
