import React, { useCallback, useContext, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';
import {
  Bot, ChevronsLeft, ChevronsRight, MessageCircle, X,
} from 'lucide-react';
import EntityChat from '../EntityChat';
import { useWorkspace } from '../workspace';
import { routeTitleKey } from '../routeTitles';
import { useI18n } from '../../i18n';
import { clearPageChat, getPageChat, pageChatUrl, stopPageChat } from '../../api';
import { describeRoute } from './registry';
import { PageChatContext, usePageChatPanel } from './pageChat';

/**
 * The chat that follows the user around: a button in the corner, and the panel
 * it opens.
 *
 * What the panel shows depends on where the user is, and there are exactly two
 * cases:
 *
 * * **The page has a chat of its own** — a scenario's builder, the Service
 *   Agent, the memory pool's reader. That chat is not replaced by a general
 *   one, because its agent is the point of it. The panel hosts it, the page
 *   folds its own column away, and the conversation carries on: same endpoint,
 *   same thread, different frame.
 * * **The page has none** — then this is one fixed assistant, the same on every
 *   such page, holding the records that page is showing (see
 *   `routes/page_chat.py`). Nothing to pick and nothing to configure: the user
 *   asks about what is in front of them and it already knows what that is.
 *
 * The Chat page itself is the one place with no button. A floating chat over a
 * full-page chat would be two composers arguing about which one the user meant.
 * For the same reason the button stands down wherever a page is showing a chat
 * of its own (`useInlineChatOpen`), and comes back when that chat is closed.
 */
export default function PageChatPanel() {
  const { t } = useI18n();
  const location = useLocation();
  const { selectedWorkspace } = useWorkspace();
  const ctx = useContext(PageChatContext);
  const { open, setOpen, wide, setWide, inlineChatOpen } = usePageChatPanel();

  const route = useMemo(
    () => describeRoute(location.pathname, location.search),
    [location.pathname, location.search],
  );
  const titleKey = routeTitleKey(location.pathname);
  const pageTitle = titleKey ? t(titleKey) : '';

  // The page's own chat, when it registered one. Read from the ref rather than
  // from state so the callbacks are this render's, not the ones that happened
  // to be current when the signature last changed.
  const entityChat = ctx?.entitySig ? ctx.entityRef.current : null;
  const subject = ctx?.subjectSig ? ctx.subjectRef.current : null;

  const isChatPage = /^\/(chat(\/.*)?)?$/.test(location.pathname);

  // The page assistant's own endpoints, bound to the route's scope. Stable
  // across renders because EntityChat reloads its transcript whenever they
  // change; they are reached only when the page registered no chat of its own.
  const pageScope = route.scope;
  // Slot in this header for the chat's Clear, so it sits with the panel's own
  // controls rather than on a row of its own above the feed. State, not a ref:
  // the chat has to re-render once the node exists to portal into.
  const [clearSlot, setClearSlot] = useState(null);
  const loadPage = useCallback(() => getPageChat(pageScope), [pageScope]);
  const clearPage = useCallback(() => clearPageChat(pageScope), [pageScope]);
  const stopPage = useCallback(() => stopPageChat(pageScope), [pageScope]);

  const scope = entityChat?.scope || pageScope;

  // What travels with a page-chat turn. Rebuilt on every render on purpose:
  // EntityChat reads it when the message is sent, so a filter changed while the
  // user was typing is still the filter the agent is told about.
  const body = entityChat ? null : {
    scope: pageScope,
    route: `${location.pathname}${location.search || ''}`,
    title: subject?.title || pageTitle,
    refs: subject?.refs || route.refs,
    hints: subject?.hints || '',
    workspace: selectedWorkspace || null,
  };

  if (isChatPage) return null;

  const title = entityChat?.title || pageTitle || t('pageChat.title');
  const suggestions = entityChat?.suggestions || [
    t('pageChat.suggestWhatIsHere'),
    t('pageChat.suggestExplain'),
    t('pageChat.suggestNext'),
  ];
  const emptyHint = entityChat?.emptyHint
    || (pageTitle ? t('pageChat.emptyHintOn', { page: pageTitle }) : t('pageChat.emptyHint'));

  // A chat the page draws itself is already on screen, with its composer in
  // the corner the launcher floats over. Nothing to add and nowhere to put it.
  if (!open && inlineChatOpen) return null;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={t('pageChat.openHint')}
        aria-label={t('pageChat.open')}
        className="fixed bottom-6 right-6 z-40 w-14 h-14 rounded-full bg-indigo-600 text-white
                   shadow-lg shadow-indigo-600/30 flex items-center justify-center
                   hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400
                   focus:ring-offset-2 transition-colors"
      >
        <MessageCircle className="w-6 h-6" />
      </button>
    );
  }

  return (
    <aside
      className={`fixed inset-y-0 right-0 z-40 flex flex-col bg-white border-l border-gray-200
                  shadow-2xl w-full ${wide ? 'sm:w-[40rem]' : 'sm:w-[26rem]'}`}
      aria-label={t('pageChat.title')}
    >
      <header className="flex items-center gap-2 px-4 py-3 border-b border-gray-200 shrink-0">
        <Bot className="w-4 h-4 text-indigo-600 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900 truncate">{title}</div>
          <div className="text-[11px] text-gray-500 truncate">
            {entityChat ? t('pageChat.pageOwnChat') : t('pageChat.aboutThisPage')}
          </div>
        </div>
        <span ref={setClearSlot} className="flex items-center shrink-0" />
        <button
          type="button"
          onClick={() => setWide((w) => !w)}
          title={wide ? t('pageChat.narrow') : t('pageChat.widen')}
          className="hidden sm:inline-flex p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100"
        >
          {wide ? <ChevronsRight className="w-4 h-4" /> : <ChevronsLeft className="w-4 h-4" />}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          title={t('pageChat.close')}
          className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100"
        >
          <X className="w-4 h-4" />
        </button>
      </header>

      {/* Keyed on the conversation: moving to another page is another thread,
          and a feed left over from the previous one must not flash in the new
          one before its transcript loads. */}
      <EntityChat
        key={`${entityChat ? 'entity' : 'page'}:${scope}`}
        path={entityChat ? entityChat.path : pageChatUrl()}
        body={body}
        loadChat={entityChat ? entityChat.loadChat : loadPage}
        clearChat={entityChat ? entityChat.clearChat : clearPage}
        stopChat={entityChat ? entityChat.stopChat : stopPage}
        onEvent={entityChat?.onEvent || null}
        registerSend={entityChat?.registerSend || null}
        header={false}
        clearTarget={clearSlot}
        inline={false}
        emptyHint={emptyHint}
        suggestions={suggestions}
        heightClass="min-h-0 max-h-none"
        className="flex-1 min-h-0 p-4"
        composerClassName="mt-auto pt-3"
      />
    </aside>
  );
}
