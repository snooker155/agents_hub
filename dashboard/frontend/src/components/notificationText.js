/**
 * A notification's title and text in the reader's language.
 *
 * The backend does not know which language the reader uses, so a notification
 * it raises also carries `source.message`: a key and its values
 * (managers/runs/notifications.py, notification_message). The text is built
 * here from components.notifications.messages.<key>; the stored title and body
 * stay as the fallback for an older notification or a key this build does not
 * know.
 */

// Which body a message uses when a value it names may be missing.
const BODY_VARIANT = {
  approvalNeeded: (p) => (p.reason ? 'bodyWithReason' : 'body'),
  taskFailed: (p) => (p.error ? 'body' : 'bodyNoError'),
  routingFailed: (p) => (p.error ? 'body' : 'bodyNoError'),
  taskNeedsInput: (p) => (p.question ? 'body' : 'bodyNoQuestion'),
};

export function notificationText(n, t) {
  const message = n?.source?.message;
  if (!message?.key) return { title: n?.title || '', body: n?.body || '' };
  const params = message.params || {};
  const base = `components.notifications.messages.${message.key}`;
  const variant = BODY_VARIANT[message.key]?.(params) || 'body';
  return {
    title: t(`${base}.title`, { ...params, defaultValue: n.title || '' }),
    body: t(`${base}.${variant}`, { ...params, defaultValue: n.body || '' }),
  };
}
