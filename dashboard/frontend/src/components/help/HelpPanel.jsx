import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Compass, LifeBuoy, X } from 'lucide-react';
import EntityChat from '../EntityChat';
import { useWorkspace } from '../workspace';
import { routeTitleKey } from '../routeTitles';
import { usePageChatPanel } from '../pageChat/pageChat';
import { useWelcomeTour } from '../docs/WelcomeTour';
import { isTourDone } from '../docs/tourRunner';
import { useI18n } from '../../i18n';
import { clearHelpChat, getHelpChat, helpChatUrl, stopHelpChat } from '../../api/help';
import { linkSegment } from './helpLinks';
import ChatMarkdown from '../chat/ChatMarkdown';

const PANEL_ID = 'help-panel';

function tourDone() {
  try {
    return isTourDone();
  } catch {
    return null;
  }
}

/**
 * The agent's reply with its links made live: a route opens that page in the
 * app (the panel stays open beside it), `#tour` starts the welcome tour, a web
 * link opens in a new tab.
 */
function HelpReply({ text, onNavigate, onTour }) {
  // Markdown like every other chat reply (steps come as lists), with links
  // classified the Help way: an app route navigates and keeps the panel
  // open, #tour starts the tour, a web link opens a tab, anything else is text.
  const HelpLink = useCallback(({ href, children }) => {
    const seg = linkSegment('', href || '');
    if (seg.type === 'nav' || seg.type === 'tour') {
      return (
        <button
          type="button"
          onClick={() => (seg.type === 'nav' ? onNavigate(seg.to) : onTour())}
          className="inline text-indigo-600 dark:text-indigo-300 underline hover:text-indigo-800 focus:outline-none focus:ring-2 focus:ring-indigo-400 rounded-sm"
        >
          {children}
        </button>
      );
    }
    if (seg.type === 'external') {
      return <a href={seg.href} target="_blank" rel="noreferrer noopener">{children}</a>;
    }
    return <>{children}</>;
  }, [onNavigate, onTour]);
  // The feed bubble keeps whitespace for plain replies; markdown lays out its own.
  return <div className="whitespace-normal"><ChatMarkdown content={text} linkComponent={HelpLink} /></div>;
}

/**
 * Help: a button in the header on every page, and the panel it opens with the
 * Support agent (routes/help_chat.py, docs/help.md).
 *
 * Not the page chat. That one is about the records on the page and keeps a
 * thread per page; this one is about the product and keeps one thread per
 * user, so the panel can stay open while its links take the user from page to
 * page. Opening it closes the page chat panel, and while it is open the page
 * chat's corner button stands down (EntityChat counts as an inline chat), so
 * the two never stack on the same edge.
 */
export default function HelpPanel() {
  const { t } = useI18n();
  const location = useLocation();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace();
  const pageChat = usePageChatPanel();
  const tour = useWelcomeTour();
  const [open, setOpen] = useState(false);
  const [clearSlot, setClearSlot] = useState(null);
  const buttonRef = useRef(null);
  const panelRef = useRef(null);

  const close = useCallback(() => {
    setOpen(false);
    buttonRef.current?.focus();
  }, []);

  const toggle = () => {
    if (!open) pageChat.setOpen(false);
    setOpen((o) => !o);
  };

  // Escape closes the panel from anywhere inside it.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape' && panelRef.current?.contains(document.activeElement)) close();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, close]);

  const startTour = useCallback(() => {
    setOpen(false);
    tour.start();
  }, [tour]);

  const goTo = useCallback((to) => navigate(to), [navigate]);
  const renderReply = useCallback(
    (text) => <HelpReply text={text} onNavigate={goTo} onTour={startTour} />,
    [goTo, startTour],
  );

  const titleKey = routeTitleKey(location.pathname);
  // Read at send time by EntityChat, so it is the page the user is on when
  // they ask, not the one they opened the panel on.
  const body = {
    route: `${location.pathname}${location.search || ''}`,
    title: titleKey ? t(titleKey) : '',
    workspace: selectedWorkspace || null,
    tour_done: tourDone(),
  };

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={toggle}
        title={t('help.openHint')}
        aria-label={t('help.open')}
        aria-expanded={open}
        aria-controls={PANEL_ID}
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 ${
          open
            ? 'bg-indigo-50 border-indigo-200 text-indigo-700'
            : 'border-gray-200 text-gray-600 hover:bg-gray-50 hover:text-gray-900'
        }`}
      >
        <LifeBuoy className="w-4 h-4" />
        <span>{t('help.button')}</span>
      </button>

      {open && (
        <aside
          id={PANEL_ID}
          ref={panelRef}
          role="dialog"
          aria-modal="false"
          aria-labelledby={`${PANEL_ID}-title`}
          className="fixed inset-y-0 right-0 z-50 flex flex-col bg-white border-l border-gray-200 shadow-2xl w-full sm:w-[26rem]"
        >
          <header className="flex items-center gap-2 px-4 py-3 border-b border-gray-200 shrink-0">
            <LifeBuoy className="w-4 h-4 text-indigo-600 shrink-0" />
            <div className="min-w-0 flex-1">
              <div id={`${PANEL_ID}-title`} className="text-sm font-semibold text-gray-900 truncate">
                {t('help.title')}
              </div>
              <div className="text-[11px] text-gray-500 truncate">{t('help.subtitle')}</div>
            </div>
            <button
              type="button"
              onClick={startTour}
              title={t('help.tourHint')}
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium text-indigo-600 hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              <Compass className="w-3.5 h-3.5" />
              {t('help.tour')}
            </button>
            <span ref={setClearSlot} className="flex items-center shrink-0" />
            <button
              type="button"
              onClick={close}
              title={t('help.close')}
              aria-label={t('help.close')}
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              <X className="w-4 h-4" />
            </button>
          </header>

          <EntityChat
            path={helpChatUrl()}
            body={body}
            loadChat={getHelpChat}
            clearChat={clearHelpChat}
            stopChat={stopHelpChat}
            header={false}
            clearTarget={clearSlot}
            emptyHint={t('help.emptyHint')}
            suggestions={[t('help.suggestWhatCanIDo'), t('help.suggestSetupNext'), t('help.suggestHowDo')]}
            renderReply={renderReply}
            heightClass="min-h-0 max-h-none"
            className="flex-1 min-h-0 p-4"
            composerClassName="mt-auto pt-3"
          />
        </aside>
      )}
    </>
  );
}
