import { useCallback, useMemo, useState } from 'react';
import { insertMention, mentionAt, mentionSuggestions, takeMention } from './agentMention';

/**
 * The `@agent` list of one composer (agentMention.js): open while the caret
 * is in an `@word`, arrows move through it, Enter or Tab picks, Escape closes
 * it until the text changes again. `named` is the agent the text hands over
 * to as it stands, for the chip above the box.
 *
 * The composer calls `onChange(event)` from its textarea's change handler and
 * `onKeyDown(event)` first in its key handler, which says whether it took the
 * key. `agents` are `{id, name}`.
 */
export function useAgentMention({ text, setText, textareaRef, agents }) {
  const [mention, setMention] = useState(null);
  const [index, setIndex] = useState(0);

  const items = useMemo(
    () => (mention ? mentionSuggestions(agents, mention.query) : []),
    [agents, mention],
  );
  const open = items.length > 0;
  const named = useMemo(() => takeMention(text, agents)?.agent || null, [agents, text]);

  const onChange = useCallback((e) => {
    const el = e?.target;
    setMention(el ? mentionAt(el.value, el.selectionStart ?? el.value.length) : null);
    setIndex(0);
  }, []);

  const pick = useCallback((agent) => {
    if (!mention || !agent) return;
    const el = textareaRef?.current;
    const caret = el?.selectionStart ?? String(text || '').length;
    const next = insertMention(text, caret, mention, agent.id);
    setText(next.text);
    setMention(null);
    // After React writes the new value: the caret lands after the mention.
    setTimeout(() => {
      const box = textareaRef?.current;
      if (!box) return;
      box.focus();
      box.setSelectionRange(next.caret, next.caret);
    }, 0);
  }, [mention, setText, text, textareaRef]);

  const close = useCallback(() => setMention(null), []);

  const onKeyDown = useCallback((e) => {
    if (!open) return false;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setIndex((i) => Math.min(i + 1, items.length - 1));
      return true;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setIndex((i) => Math.max(i - 1, 0));
      return true;
    }
    if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
      e.preventDefault();
      pick(items[Math.min(index, items.length - 1)]);
      return true;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      setMention(null);
      return true;
    }
    return false;
  }, [index, items, open, pick]);

  return { open, items, index: Math.min(index, Math.max(items.length - 1, 0)), setIndex, pick, close, onChange, onKeyDown, named };
}

export default useAgentMention;
