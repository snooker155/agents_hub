/**
 * Chat text helpers: the structured-block stripper the send path uses, and
 * `renderContent`, which renders a reply as markdown (ChatMarkdown.jsx).
 */
import ChatMarkdown from './ChatMarkdown';

// ---------------------------------------------------------------------------
// A structured-response block (<<<ui>>>{json}<<<end>>>). The backend strips it
// from the final response, but during streaming the raw tokens still carry it;
// strip it from any fallback content so the markers never show in the bubble.
const UI_BLOCK_RE = /<<<\s*ui\s*>>>[\s\S]*?<<<\s*\/?\s*end\s*>>>/gi;

const stripUiBlock = (s) => (s || '').replace(UI_BLOCK_RE, '').trim();

// Kept as a function so the build view's call sites read as before.
function renderContent(text, { streaming = false } = {}) {
  return <ChatMarkdown content={text} streaming={streaming} />;
}

export { stripUiBlock, renderContent };
