/**
 * The name a signed-in person is shown by: first and last name when the
 * account has them, else the display name, else the login.
 */
export function accountName(user) {
  if (!user) return '';
  const full = [user.first_name, user.last_name].map((s) => (s || '').trim()).filter(Boolean).join(' ');
  return full || user.display_name || user.username || '';
}
