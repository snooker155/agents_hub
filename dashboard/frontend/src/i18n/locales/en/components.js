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
    minutesAgo: '{{count}}m ago',
    hoursAgo: '{{count}}h ago',
    daysAgo: '{{count}}d ago',
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
};
