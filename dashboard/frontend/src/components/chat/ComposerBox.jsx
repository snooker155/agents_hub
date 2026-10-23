/**
 * The frame of the Chat page's message box, for every box a message is
 * written in: the line (what goes before the text, the text, the buttons
 * after it) and, past a rule under it, a footer of three parts, the middle
 * one centred whatever the sides hold. The pieces inside take their look
 * from composerStyles.js.
 *
 * @param {object} props
 * @param {React.ReactNode} [props.leading]      before the text (the attach button).
 * @param {React.ReactNode} props.children       the text area.
 * @param {React.ReactNode} [props.actions]      after the text (Send, Stop).
 * @param {React.ReactNode} [props.footerLeft]   the footer's left part (auto mode).
 * @param {React.ReactNode} [props.hint]         the footer's middle: how to type.
 * @param {string} [props.hintTitle]             the hint in full, on hover, when it is cut.
 * @param {React.ReactNode} [props.footerRight]  the footer's right part (the context meter).
 * @param {boolean} [props.highlight]            an amber frame (auto mode on).
 * @param {string} [props.testId]
 */
export default function ComposerBox({
  leading = null, children, actions = null, footerLeft = null, hint = null, hintTitle,
  footerRight = null, highlight = false, testId = 'composer-box',
}) {
  return (
    <div
      data-testid={testId}
      className={`border rounded-2xl bg-white shadow-lg transition-all ${highlight ? 'border-amber-400' : 'border-gray-400'}`}
    >
      <div className="flex items-center gap-3 px-4 py-3">
        {leading}
        {children}
        {actions}
      </div>
      {(footerLeft || hint || footerRight) && (
        <div className="grid grid-cols-[1fr_minmax(0,max-content)_1fr] items-center gap-3 border-t border-gray-100 px-3 py-1.5 text-xs">
          <div className="flex min-w-0 justify-start">{footerLeft}</div>
          <p className="min-w-0 truncate text-center text-[11px] text-gray-400" title={hintTitle}>{hint}</p>
          <div className="flex min-w-0 justify-end">{footerRight}</div>
        </div>
      )}
    </div>
  );
}
