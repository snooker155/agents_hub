import { ChevronDown } from 'lucide-react';

/**
 * A select drawn in small type, phone included. On a phone every field is
 * 16px (index.css: iOS zooms into a smaller one when it gets focus), so a
 * filter in a toolbar came out larger than the text around it. Here the
 * chosen option is shown as plain text at the size asked for, and the native
 * select lies over it, transparent and still 16px: a tap opens the phone's
 * own list, with no zoom.
 *
 * @param {string} props.value
 * @param {function} props.onChange (value) => void
 * @param {{value: string, label: string}[]} props.options
 * @param {string} props.label the accessible name
 * @param {string} [props.className] the box's size and place
 */
export default function CompactSelect({ value, onChange, options, label, className = '' }) {
  const current = options.find((o) => o.value === value) || options[0];
  return (
    <div className={`relative inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white pl-3 pr-2 py-2 phone:py-1.5 text-sm phone:text-xs text-gray-700 focus-within:ring-2 focus-within:ring-indigo-200 ${className}`}>
      <span className="truncate">{current?.label}</span>
      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-gray-400" aria-hidden="true" />
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
      >
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}
