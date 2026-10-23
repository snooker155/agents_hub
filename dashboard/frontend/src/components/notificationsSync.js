/*
 * Read state of notifications changed in this tab (a mark read, mark all, a
 * delete). The bell in the header keeps its own count and the Plan page its
 * own list, so whichever acts announces it and the other reloads.
 */
export const NOTIFICATIONS_CHANGED = 'hub:notifications-changed';

export function announceNotificationsChanged(source) {
  try { window.dispatchEvent(new CustomEvent(NOTIFICATIONS_CHANGED, { detail: { source } })); } catch { /* no window */ }
}
