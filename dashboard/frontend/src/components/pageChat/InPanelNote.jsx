import React from 'react';
import { PanelRightClose } from 'lucide-react';
import { useI18n } from '../../i18n';
import { usePageChatPanel } from './pageChat';

/**
 * What a page shows where its chat used to be, while the floating panel is
 * holding that same conversation.
 *
 * Pages whose chat sits in a side column get this for free — `useChatColumn`
 * folds the column away. Pages that put their chat in a tab or a bespoke aside
 * have nowhere to fold it to, so they say where it went instead of leaving a
 * blank panel, with the one control that brings it back.
 */
export default function InPanelNote({ className = '' }) {
  const { t } = useI18n();
  const { setOpen } = usePageChatPanel();

  return (
    <div className={`flex flex-col items-start gap-2 text-xs text-gray-500 ${className}`}>
      <p>{t('pageChat.inPanel')}</p>
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="inline-flex items-center gap-1.5 text-[11px] font-medium text-indigo-700
                   bg-indigo-50 border border-indigo-100 rounded-md px-2.5 py-1.5 hover:bg-indigo-100"
      >
        <PanelRightClose className="w-3.5 h-3.5" /> {t('pageChat.bringBack')}
      </button>
    </div>
  );
}
