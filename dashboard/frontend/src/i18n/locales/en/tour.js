export default {
  next: 'Next',
  previous: 'Back',
  done: 'Finish',
  progress: '{{current}} of {{total}}',
  steps: {
    assistant: {
      title: 'Assistant',
      description: 'The whole service through one agent, by voice or by text. Hold the button or Space to speak; the answer is read aloud and its links open beside the conversation.',
    },
    chat: {
      title: 'Chat',
      description: 'Talk to any agent, flow or team from here. The header shows the workspace, the project and the model your next message will use.',
    },
    agents: {
      title: 'Agents',
      description: 'Every agent is a folder of layered instructions with tools granted by name. Each card shows what it holds and what it is running now.',
    },
    tasks: {
      title: 'Tasks',
      description: 'Work that outlives a conversation: subtasks, dependencies, the agent on it and its result. Switch between the list and the board.',
    },
    flows: {
      title: 'Flows',
      description: 'A graph of agents run as one pipeline. Open a flow to edit it on a canvas and run it against a task.',
    },
    teams: {
      title: 'Teams',
      description: 'A roster of agents working over one shared message board, which is both the work and the record of how it went.',
    },
    playground: {
      title: 'Playground',
      description: 'Agents acting in a simulated world, tick by tick, with the thought behind each move and the state it changed.',
    },
    artifacts: {
      title: 'Artifacts',
      description: 'What the agents made: the views they built as answers (charts, graphs, 3D scenes, decks, documents) and the workspace\'s files, as two tabs of one page. Open a view to keep editing it in conversation.',
    },
    health: {
      title: 'Health',
      description: 'Whether the service, the models and the optional parts are up, with a fix suggested for anything that is not.',
    },
    docs: {
      title: 'Docs',
      description: 'The full documentation, the same text agents read. You can replay this tour from here at any time.',
    },
  },
};
