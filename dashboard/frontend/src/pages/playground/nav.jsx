import React from 'react';
import { Link } from 'react-router-dom';
import { Gamepad2, Globe2 } from 'lucide-react';
import { useI18n } from '../../i18n';

/**
 * Scenarios ⇄ Worlds, as one control beside the page title.
 *
 * The two are halves of the same catalogue — a scenario is a cast and a set of
 * limits, the world is the place it plays out in — and they were reachable only
 * through a link buried among the page's other buttons, which reads as "go
 * somewhere else" rather than "look at the other half of this". A segmented
 * switch on the heading says what it is: two views of the playground, one of
 * which you are on.
 */
export default function PlaygroundSwitch({ active }) {
  const { t } = useI18n();
  const tabs = [
    { key: 'scenarios', to: '/playground', label: t('playground.scenarios'), icon: Gamepad2 },
    { key: 'worlds', to: '/playground/worlds', label: t('worlds.title'), icon: Globe2 },
  ];
  return (
    <nav className="inline-flex items-center gap-0.5 rounded-lg bg-gray-100 p-0.5">
      {tabs.map(({ key, to, label, icon: Icon }) => (
        <Link
          key={key}
          to={to}
          aria-current={key === active ? 'page' : undefined}
          className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
            key === active
              ? 'bg-white text-indigo-700 shadow-sm'
              : 'text-gray-500 hover:text-gray-800'
          }`}
        >
          <Icon className="h-3.5 w-3.5" /> {label}
        </Link>
      ))}
    </nav>
  );
}
