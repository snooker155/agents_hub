/**
 * What this browser remembers about its session beyond the token itself:
 * how it was opened (`kind`: "oidc" or "password") and when it ends.
 *
 * Only single sign-on sessions need it. They are short (AUTH_OIDC_SESSION_HOURS)
 * and renewed silently by sending the browser through the provider again a
 * little before they end (AuthContext.jsx); for that the app has to know the
 * expiry without asking the backend on every tick. A module of its own so the
 * callback page (lazy loaded) and the auth provider (always loaded) share the
 * key without one importing the other.
 */
export const SESSION_META_KEY = 'agents_hub_session_meta';

/** Renew when less than this is left. */
export const RENEW_BEFORE_MS = 15 * 60 * 1000;
/** How often an open tab checks. */
export const RENEW_CHECK_MS = 5 * 60 * 1000;

export const readSessionMeta = () => {
  try {
    const raw = window.localStorage.getItem(SESSION_META_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
};

export const writeSessionMeta = (meta) => {
  try {
    if (meta) window.localStorage.setItem(SESSION_META_KEY, JSON.stringify(meta));
    else window.localStorage.removeItem(SESSION_META_KEY);
  } catch {
    // Privacy mode: no renewal, the session simply ends at its expiry.
  }
};

/** True when an OIDC session ends within RENEW_BEFORE_MS of `now`. */
export const oidcRenewalDue = (meta, now = Date.now()) => {
  if (!meta || meta.kind !== 'oidc' || !meta.expires_at) return false;
  const ends = Date.parse(meta.expires_at);
  if (Number.isNaN(ends)) return false;
  return ends - now < RENEW_BEFORE_MS;
};

/** A post-login destination the app may navigate to: a path on this origin. */
export const safeNextPath = (value) => {
  const next = String(value || '');
  if (!next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return '/';
  return next;
};

/** The key/value pairs of a URL fragment (`#a=1&b=2`). */
export const parseFragment = (hash) => {
  const out = {};
  const text = String(hash || '').replace(/^#/, '');
  if (!text) return out;
  new URLSearchParams(text).forEach((value, key) => { out[key] = value; });
  return out;
};

/** The message for an error code from the SSO callback, never the raw code. */
export const ssoErrorMessage = (t, code) => {
  const key = `auth.sso.errors.${code}`;
  const text = t(key);
  return text && text !== key ? text : t('auth.sso.errors.unknown');
};
