import ChatMarkdown from './chat/ChatMarkdown';
import { looksLikeMarkdown } from './processUtils';

// A step's text (an agent's output): rendered as markdown when it is written
// in markdown, the way the chat shows the same reply, and as plain text with
// its line breaks otherwise.
export default function StepText({ text, className = 'text-[11px] text-gray-700' }) {
  if (text && looksLikeMarkdown(text)) {
    return <div className={className}><ChatMarkdown content={text} /></div>;
  }
  return <div className={`${className} whitespace-pre-wrap break-words`}>{text || '(empty)'}</div>;
}
