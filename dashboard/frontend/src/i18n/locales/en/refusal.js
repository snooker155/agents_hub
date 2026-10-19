// A turn the capability guard or a spend limit refused (chat/refusals.py,
// components/chat/RefusalCard.jsx): the reason in plain words and the action
// in place, instead of a red error string.
export default {
  guard: {
    title: 'This agent is not allowed to run with these tools',
    body: 'Together, this agent {{caps}}. That combination lets text from outside steer the agent into leaking your data, so the safety check stopped the turn.',
    capsJoin: ' and ',
    allow: 'Allow for this agent',
    allowing: 'Allowing...',
    allowed: 'Allowed for this agent. The check now lets it run.',
    allowedButBlocked: 'Saved, but the check still stops it: in this mode the exception only applies when the agent runs in a container without network.',
    noOverride: 'This rule cannot be switched off for this agent.',
    failed: 'Could not save the exception',
    details: 'Details',
  },
  retry: 'Try again',
  retryHint: 'Send the same message again.',
};
