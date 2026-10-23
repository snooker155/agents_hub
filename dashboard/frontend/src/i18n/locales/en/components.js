export default {
  notifications: {
    title: 'Notifications',
    tooltip: 'Notifications',
    markReadFailed: 'Could not mark as read',
    markAllRead: 'Mark all read',
    empty: 'No notifications',
    viewTask: 'view task →',
    viewAllInPlan: 'View all in Plan →',
    justNow: 'just now',
    newNotification: 'New notification',
    minutesAgo: '{{count}}m ago',
    hoursAgo: '{{count}}h ago',
    daysAgo: '{{count}}d ago',
    messages: {
      taskStarted: {
        title: 'Task started: {{title}}',
        body: 'Agent \'{{agent}}\' started the task.',
      },
      taskCompleted: {
        title: 'Task completed: {{title}}',
        body: 'Agent \'{{agent}}\' finished the task.',
      },
      taskFailed: {
        title: 'Task failed: {{title}}',
        body: 'Agent \'{{agent}}\' failed: {{error}}',
        bodyNoError: 'Agent \'{{agent}}\' failed.',
      },
      routingFailed: {
        title: 'Routing failed: {{title}}',
        body: 'The orchestrator could not route the task: {{error}}',
        bodyNoError: 'The orchestrator could not route the task.',
      },
      approvalNeeded: {
        title: 'Approval needed: {{title}}',
        body: 'Approve it on the task page so agent \'{{agent}}\' starts.',
        bodyWithReason: 'Approve it on the task page so agent \'{{agent}}\' starts. Why this agent: {{reason}}',
      },
      containerCompleted: {
        title: 'Task completed: {{title}}',
        body: 'All subtasks are done ({{count}}).',
      },
      taskNeedsInput: {
        title: 'A task needs your input',
        body: '{{question}}',
        bodyNoQuestion: 'The agent is waiting for your answer.',
      },
      reviewCycleLimit: {
        title: 'A task hit its review cycle limit',
        body: 'The review cycle limit ({{limit}}) is reached: the task needs your decision instead of another automatic fix and review pass.',
      },
    },
  },
  inlineEdit: {
    clickToEdit: 'Click to edit',
    clickToAdd: 'Click to add…',
  },
  runOrigin: {
    external: 'External',
    imported: 'imported',
    partial: 'partial',
    externalHint: 'Reported by the connection {{connection}}; this hub did not run it, so it has no log or live stream of its own.',
    importedHint: 'Recorded from OpenTelemetry spans after the run had already finished, so there is no live view of it.',
    partialHint: 'Its root span never arrived, so the input, the answer and the outcome may be missing.',
  },
  cronHint: {
    next: 'Next: {{times}}',
    checking: 'Checking…',
  },
};
