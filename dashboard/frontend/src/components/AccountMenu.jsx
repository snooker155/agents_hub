import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Check, ChevronLeft, ChevronRight, CircleQuestionMark, LogOut, Moon, Palette, Pause, Radio,
  Sun, UserRound,
} from 'lucide-react';
import { useI18n, LANGUAGES } from '../i18n';
import { useTheme } from './theme';
import { MULTI, useAuth } from './auth';
import { SIMPLE, FULL } from './navMenu';
import InstallAppButton from './InstallAppButton';
import { accountName } from './accountName';

function initials(name) {
  const parts = String(name || '?').split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : String(name || '?').slice(0, 2);
  return letters.toUpperCase();
}

const STATUS_DOT = {
  online: 'bg-green-500',
  paused: 'bg-gray-400',
  offline: 'bg-red-500',
};

const rowClass = 'w-full flex items-center gap-3 px-4 py-2.5 text-sm text-gray-800 hover:bg-gray-200 hover:text-gray-900 transition-colors text-left';

/**
 * The account at the foot of the sidebar: the avatar, the person's name and
 * the live status of the hub beside it. A click opens the account menu:
 * Profile (the /account page, multi mode only), Personalization (theme,
 * interface language, simple or full menu), Help, installing the app (when
 * the browser offers it), pausing the live updates, and Sign out.
 *
 * Outside multi mode there is nobody signed in: the row stands for the one
 * operator of the install, and the menu has no Profile and no Sign out.
 *
 * `status` is online, paused or offline (the shared stream, see Layout);
 * `menuMode`, `onMenuModeChange` and `moreCount` are the sidebar's map
 * (navMenu.js: simple or full, and how many rows the full one adds);
 * `compact` is the folded sidebar, where only the avatar shows and the menu
 * opens beside the sidebar instead of above the row. Open, the menu is
 * placed against the account block of the sidebar (`relative` there, not
 * here), so it is as wide as the sidebar.
 */
export default function AccountMenu({
  status = 'online', onToggleLive, onHelp, compact = false, menuMode, onMenuModeChange, moreCount = 0,
}) {
  const { t, language, setLanguage } = useI18n();
  const { theme, setTheme } = useTheme();
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const [view, setView] = useState('main');
  const ref = useRef(null);

  const signedIn = auth.mode === MULTI && Boolean(auth.user);
  const name = signedIn ? accountName(auth.user) : t('layout.account.operator');
  const statusLabel = t(`layout.live.${status}`);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const toggle = () => {
    setView('main');
    setOpen((o) => !o);
  };

  const avatar = (
    <span className="relative shrink-0">
      <span className="h-8 w-8 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-xs">
        {signedIn ? initials(name) : <UserRound className="w-4 h-4" />}
      </span>
      {/* Folded, the status rides the avatar since there is no name beside it. */}
      {compact && (
        <span className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full ring-2 ring-white ${STATUS_DOT[status]}`} />
      )}
    </span>
  );

  return (
    <div ref={ref} className={compact ? 'relative' : 'flex-1 min-w-0'}>
      <button
        type="button"
        onClick={toggle}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('layout.account.menu')}
        title={compact ? `${name} · ${statusLabel}` : undefined}
        className={`w-full flex items-center gap-2.5 rounded-lg p-1.5 text-left transition-colors ${
          open ? 'bg-indigo-50' : 'hover:bg-gray-100'
        } ${compact ? 'justify-center' : ''}`}
      >
        {avatar}
        {!compact && (
          <>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5 min-w-0">
                <span className="truncate text-sm font-semibold text-gray-800" data-testid="account-name">{name}</span>
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${STATUS_DOT[status]} ${status === 'online' ? 'animate-pulse' : ''}`}
                  title={statusLabel}
                  data-testid="account-status"
                  data-status={status}
                />
              </span>
              {signedIn && auth.user.username && auth.user.username !== name && (
                <span className="block truncate text-[11px] text-gray-400">{auth.user.username}</span>
              )}
            </span>
          </>
        )}
      </button>

      {open && (
        <div
          role="menu"
          className={`absolute z-50 bg-gray-100 border border-gray-300 rounded-xl shadow-xl py-1 ${
            compact ? 'left-full bottom-0 ml-3 w-64' : 'left-0 right-0 bottom-full mb-1'
          }`}
        >
          {view === 'main' ? (
            <>
              <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-300 mb-1">
                <span className="h-10 w-10 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-sm shrink-0">
                  {signedIn ? initials(name) : <UserRound className="w-4 h-4" />}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-gray-900">{name}</span>
                  <span className="flex items-center gap-1.5 text-[11px] text-gray-500">
                    <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[status]}`} />
                    {status === 'offline' ? t('layout.live.backendOffline') : statusLabel}
                  </span>
                </span>
              </div>
              {signedIn && (
                <Link to="/account" role="menuitem" onClick={() => setOpen(false)} className={rowClass}>
                  <UserRound className="w-4 h-4 text-gray-500" /> {t('nav.account')}
                </Link>
              )}
              <button type="button" role="menuitem" onClick={() => setView('personalize')} className={rowClass}>
                <Palette className="w-4 h-4 text-gray-500" />
                <span className="flex-1">{t('layout.account.personalization')}</span>
                <ChevronRight className="w-4 h-4 text-gray-400" />
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => { setOpen(false); onHelp?.(); }}
                title={t('help.openHint')}
                className={rowClass}
              >
                <CircleQuestionMark className="w-4 h-4 text-gray-500" /> {t('layout.account.help')}
              </button>
              <InstallAppButton menuItem />
              <button
                type="button"
                role="menuitem"
                disabled={status === 'offline'}
                onClick={() => onToggleLive?.()}
                className={`${rowClass} disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                {status === 'paused'
                  ? <><Radio className="w-4 h-4 text-gray-500" /> {t('layout.live.resume')}</>
                  : <><Pause className="w-4 h-4 text-gray-500" /> {t('layout.live.pause')}</>}
              </button>
              {signedIn && (
                <>
                  <div className="my-1 border-t border-gray-300" />
                  <button type="button" role="menuitem" onClick={() => { setOpen(false); auth.logout(); }} className={rowClass}>
                    <LogOut className="w-4 h-4 text-gray-500" /> {t('auth.logout')}
                  </button>
                </>
              )}
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setView('main')}
                className="w-full flex items-center gap-1.5 px-4 py-2.5 text-sm font-semibold text-gray-900 border-b border-gray-300 mb-1 hover:bg-gray-200"
              >
                <ChevronLeft className="w-4 h-4 text-gray-400" /> {t('layout.account.personalization')}
              </button>
              <p className="px-4 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                {t('layout.account.theme')}
              </p>
              <div className="px-4 pb-2 grid grid-cols-2 gap-2">
                {[['light', Sun], ['dark', Moon]].map(([mode, Icon]) => (
                  <button
                    key={mode}
                    type="button"
                    aria-pressed={theme === mode}
                    onClick={() => setTheme(mode)}
                    className={`flex items-center justify-center gap-1.5 rounded-lg border px-2 py-1.5 text-xs font-medium transition-colors ${
                      theme === mode
                        ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                        : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" /> {t(`layout.account.themes.${mode}`)}
                  </button>
                ))}
              </div>
              {/* Simple or full menu: the same pages either way, only the map. */}
              {menuMode && (
                <>
                  <p className="px-4 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400" title={t('nav.mode.hint')}>
                    {t('layout.account.menuMode')}
                  </p>
                  <div className="px-4 pb-2 grid grid-cols-2 gap-2">
                    {[[SIMPLE, t('layout.account.menuModes.simple')], [FULL, t('layout.account.menuModes.full')]].map(([mode, label]) => (
                      <button
                        key={mode}
                        type="button"
                        aria-pressed={menuMode === mode}
                        title={t('nav.mode.hint')}
                        onClick={() => onMenuModeChange?.(mode)}
                        className={`flex items-center justify-center gap-1 rounded-lg border px-2 py-1.5 text-xs font-medium transition-colors ${
                          menuMode === mode
                            ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                            : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                        }`}
                      >
                        {label}
                        {mode === FULL && moreCount > 0 && <span className="text-gray-400">{t('nav.mode.more', { count: moreCount })}</span>}
                      </button>
                    ))}
                  </div>
                </>
              )}
              <p className="px-4 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                {t('layout.language.tooltip')}
              </p>
              {LANGUAGES.map((lang) => (
                <button
                  key={lang.code}
                  type="button"
                  role="menuitemradio"
                  aria-checked={lang.code === language}
                  onClick={() => setLanguage(lang.code)}
                  className={`${rowClass} ${lang.code === language ? 'text-indigo-600 font-semibold' : ''}`}
                >
                  <span className="text-sm leading-none">{lang.flag}</span>
                  <span className="flex-1 truncate">{lang.label}</span>
                  {lang.code === language && <Check className="w-3.5 h-3.5 shrink-0" />}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
