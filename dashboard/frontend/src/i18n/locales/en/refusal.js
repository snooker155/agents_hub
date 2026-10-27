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
    allowedButBlocked: 'Saved, but in chat the check still stops it: in this mode the exception only applies to a background run in a container without network, and a chat turn runs on the hub itself.',
    noOverride: 'This rule cannot be switched off for this agent.',
    failed: 'Could not save the exception',
    details: 'Details',
  },
  guardrail: {
    titleInput: 'The message was stopped by the “{{name}}” guardrail',
    titleOutput: 'The agent’s answer was stopped by the “{{name}}” guardrail',
    found: {
      pii: 'The text contains what looks like {{what}}.',
      keywords: 'The text contains the blocked word “{{keyword}}”.',
      max_chars: 'The text is {{length}} characters long, over the limit of {{limit}}.',
      regex: 'The text matches this guardrail’s pattern.',
      judge: 'The checking model flagged the text: {{reason}}',
      judgeUnreachable: 'The checking model could not be reached, and this guardrail stops the turn when that happens.',
    },
    pii: {
      email: 'an email address',
      phone: 'a phone number',
      credit_card: 'a credit card number',
      iban: 'an IBAN',
      api_key: 'an API key or secret',
    },
    warnInput: 'The “{{name}}” guardrail flagged the message.',
    warnOutput: 'The “{{name}}” guardrail flagged the answer.',
    hintInput: 'Remove it from the message and send it again.',
    hintOutput: 'The agent cannot show this answer. Try rephrasing the request.',
  },
  retry: 'Try again',
  retryHint: 'Send the same message again.',
};
