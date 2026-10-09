/**
 * The first run's building blocks: one screen's frame (a large icon, a title,
 * a line under it, the body, the buttons at the foot), a choice card, a row
 * of pills, the primary button and the quiet "not now". Every step is drawn
 * from these, so the screens read as one sequence.
 */
import { Check, Loader2 } from 'lucide-react';

export function StepFrame({ icon: Icon, visual, title, subtitle, children, footer, testId }) {
  return (
    <section className="w-full max-w-xl mx-auto flex flex-col min-h-0 flex-1" data-testid={testId}>
      <div className="flex-1 min-h-0 overflow-y-auto px-1 pb-4">
        <div className="flex flex-col items-center text-center pt-2 sm:pt-6">
          {visual || (Icon && (
            <div className="w-16 h-16 rounded-2xl bg-indigo-50 text-indigo-600 flex items-center justify-center mb-5">
              <Icon className="w-8 h-8" />
            </div>
          ))}
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight text-gray-900">{title}</h1>
          {subtitle && <p className="mt-3 text-base text-gray-500 max-w-md leading-relaxed">{subtitle}</p>}
        </div>
        {children && <div className="mt-8">{children}</div>}
      </div>
      {footer && <div className="shrink-0 pt-4 pb-2 flex flex-col items-center gap-3">{footer}</div>}
    </section>
  );
}

export function PrimaryButton({ children, onClick, disabled, busy, testId, type = 'button' }) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || busy}
      data-testid={testId}
      className="w-full sm:w-80 flex items-center justify-center gap-2 rounded-xl bg-indigo-600 px-6 py-3 text-base font-semibold text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
    >
      {busy && <Loader2 className="w-4 h-4 animate-spin" />}
      {children}
    </button>
  );
}

export function LaterButton({ children, onClick, disabled, testId }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className="text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-40"
    >
      {children}
    </button>
  );
}

/** A large selectable row: icon, title, a line of detail, a check when chosen. */
export function ChoiceCard({ icon: Icon, title, detail, selected, onClick, badge, disabled, testId, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={selected}
      data-testid={testId}
      className={`w-full text-left flex items-start gap-4 rounded-2xl border px-4 py-4 transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
        selected ? 'border-indigo-500 bg-indigo-50/60 ring-1 ring-indigo-500' : 'border-gray-200 bg-white hover:border-gray-300'
      }`}
    >
      {Icon && (
        <span className={`w-10 h-10 shrink-0 rounded-xl flex items-center justify-center ${selected ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-600'}`}>
          <Icon className="w-5 h-5" />
        </span>
      )}
      <span className="flex-1 min-w-0">
        <span className="flex items-center gap-2 flex-wrap">
          <span className="text-base font-semibold text-gray-900">{title}</span>
          {badge && <span className="text-[11px] font-semibold uppercase tracking-wide text-indigo-700 bg-indigo-100 rounded-full px-2 py-0.5">{badge}</span>}
        </span>
        {detail && <span className="block mt-0.5 text-sm text-gray-500">{detail}</span>}
        {children}
      </span>
      <span className={`w-6 h-6 shrink-0 rounded-full border flex items-center justify-center mt-2 ${selected ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-gray-300'}`}>
        {selected && <Check className="w-4 h-4" />}
      </span>
    </button>
  );
}

/** A segmented row of short choices (providers, search services). */
export function Pills({ items, value, onChange, testId }) {
  return (
    <div className="flex flex-wrap justify-center gap-2" role="radiogroup" data-testid={testId}>
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          role="radio"
          aria-checked={value === it.id}
          onClick={() => onChange(it.id)}
          className={`rounded-full px-4 py-2 text-sm font-medium border transition-colors ${
            value === it.id ? 'bg-indigo-600 border-indigo-600 text-white' : 'bg-white border-gray-200 text-gray-700 hover:border-gray-300'
          }`}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

/** A settled fact: a green check, what is set, and what it is. */
export function DoneNote({ title, detail, testId }) {
  return (
    <div className="flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-4" data-testid={testId}>
      <span className="w-8 h-8 shrink-0 rounded-full bg-emerald-500 text-white flex items-center justify-center">
        <Check className="w-5 h-5" />
      </span>
      <div className="min-w-0">
        <p className="text-base font-semibold text-gray-900">{title}</p>
        {detail && <p className="text-sm text-gray-600 mt-0.5 break-words">{detail}</p>}
      </div>
    </div>
  );
}

export function ErrorLine({ children }) {
  if (!children) return null;
  return <p className="mt-3 text-sm text-red-600 text-center" role="alert">{children}</p>;
}
