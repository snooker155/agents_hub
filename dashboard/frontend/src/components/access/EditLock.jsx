import { Lock } from 'lucide-react';

/**
 * Wraps editing UI so a viewer who may not change it can still read it: a
 * `fieldset disabled` turns every control inside off, and the neutral classes
 * keep it from drawing a box. `locked` false renders the children untouched.
 */
export function EditLock({ locked, children }) {
  if (!locked) return children;
  return (
    <fieldset disabled className="min-w-0 border-0 p-0 m-0" data-testid="edit-lock">
      {children}
    </fieldset>
  );
}

/** The one line that says why the controls below are off. */
export function AccessNotice({ children, testId = 'access-notice' }) {
  return (
    <div data-testid={testId}
      className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
      <Lock className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{children}</span>
    </div>
  );
}
