// The pieces of the Chat page's message box, shared by every box a message
// is written in (the Chat page, a flow's chat, an instance, a team), so they
// look and behave alike. The frame itself is ComposerBox.jsx.

/** The text area: grows with the text, no frame of its own. */
export const COMPOSER_TEXTAREA_CLS = 'flex-1 min-w-0 resize-none text-base text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent leading-relaxed disabled:opacity-50';

/** Send (or Run): a round button at the end of the line. */
export const COMPOSER_SEND_CLS = 'flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

/** Stop: the same round button, red. */
export const COMPOSER_STOP_CLS = 'flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-red-100 text-red-600 hover:bg-red-200 disabled:opacity-40 transition-colors';

/** Attach: a round grey button at the start of the line. */
export const COMPOSER_ATTACH_CLS = 'w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors disabled:opacity-40';
