// The workspace's agent loop settings (agents/loop_ext/settings.py,
// `settings.loop`): compaction, tool search, and the native/strict toggles
// the loop's extensions read through `loop_setting`.
export default {
  title: 'Agent loop',
  intro: 'How the agent loop manages a long run in this workspace: when it folds and clears old context, when an agent with many tools sees a short list plus search instead of all of them, and which native provider features it uses. Unset falls back to the environment, then a built-in default.',
  fields: {
    compaction: {
      label: 'Compaction',
      hint: 'Fold and clear the context of a long run before it fills the model\'s window.',
    },
    compactionFraction: {
      label: 'Compaction fraction',
      hint: 'Share of the model\'s context window a run may fill before it is compacted, from 0.05 to 0.95.',
    },
    compactionKeep: {
      label: 'Compaction keep',
      hint: 'How many of the newest tool results always stay verbatim, at least 1.',
    },
    toolSearchThreshold: {
      label: 'Tool search threshold',
      hint: 'Above this many tools, an agent sees a short list plus search_tools instead of every tool, at least 1.',
    },
    native: {
      label: 'Native provider features',
      hint: 'Use Anthropic\'s own context editing and deferred tool loading where the model supports them.',
    },
    strictTools: {
      label: 'Strict tool schemas',
      hint: 'Ask OpenAI to enforce a tool\'s schema exactly, when every bound tool qualifies.',
    },
    viewFocus: {
      label: 'View focus',
      hint: 'Show a view agent the tools of the view kind it is on, and hide the other kinds\' tools.',
    },
    toolOutputSpillChars: {
      label: 'Tool output spill size',
      hint: 'A tool result longer than this many characters is saved to a workspace file under tool-outputs/, and the model sees its start, its end and the file. 0 turns it off.',
    },
    advisorMaxCalls: {
      label: 'Advisor calls per run',
      hint: 'How many times one run may consult the agent\'s advisor model, from 0 to 50.',
    },
    advisorMaxAnswerChars: {
      label: 'Advisor answer length',
      hint: 'The longest advisor answer handed to the agent, in characters, from 200 to 50000.',
    },
  },
  source: {
    workspace: 'this workspace',
    environment: 'the environment',
    default: 'the default',
  },
  reset: 'Reset',
  resetFor: 'Reset {{field}} to the environment or default',
  save: 'Save',
  saved: 'Agent loop settings saved.',
  saveFailed: 'Could not save the agent loop settings.',
  loadFailed: 'Could not load the agent loop settings.',
};
