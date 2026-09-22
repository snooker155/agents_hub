import { genId } from './turnState';
import { MAX_ATTACHMENT_BYTES, MAX_ATTACHMENT_COUNT, MAX_REFERENCE_COUNT } from './limits';
import { getContextKinds } from '../../api';
import { useCallback, useEffect } from 'react';

/**
 * The composer's two attachment paths: files from the computer, which the
 * browser carries, and hub records, which are pointers the server resolves at
 * send time. Plus the textarea's own auto-resize.
 */
export function useChatComposerInput(deps) {
  const {
    attachMenuOpen, contextKinds, pendingAttachments, selectedWorkspace,
    setAttachmentError, setContextKinds, setPendingAttachments, setPendingReferences,
    textareaRef,
  } = deps;

  // ---- textarea auto-resize ----
  const resizeTextarea = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
  }, [textareaRef]);

  const onPickFiles = useCallback(async (e) => {
    const picked = Array.from(e.target.files || []);
    if (!picked.length) return;

    setAttachmentError('');
    const availableSlots = Math.max(0, MAX_ATTACHMENT_COUNT - pendingAttachments.length);
    const toRead = picked.slice(0, availableSlots);
    const nextItems = [];

    for (const file of toRead) {
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setAttachmentError(`"${file.name}" exceeds 5 MB.`);
        continue;
      }
      try {
        const content = await file.text();
        nextItems.push({
          id: genId(),
          filename: file.name || `attachment_${Date.now()}.txt`,
          content,
          size: file.size,
          store_to_workspace: false,
        });
      } catch {
        setAttachmentError(`Failed to read "${file.name}".`);
      }
    }

    if (picked.length > availableSlots) {
      setAttachmentError(`Only ${MAX_ATTACHMENT_COUNT} attachments are allowed per message.`);
    }

    if (nextItems.length) {
      setPendingAttachments((prev) => [...prev, ...nextItems]);
    }
    e.target.value = '';
  }, [pendingAttachments.length, setAttachmentError, setPendingAttachments]);

  const removeAttachment = useCallback((id) => {
    setPendingAttachments((prev) => prev.filter((a) => a.id !== id));
  }, [setPendingAttachments]);

  const toggleAttachmentStore = useCallback((id, checked) => {
    setPendingAttachments((prev) =>
      prev.map((a) => (a.id === id ? { ...a, store_to_workspace: checked } : a)),
    );
  }, [setPendingAttachments]);

  // ---- context references (hub entities attached to the next message) ----
  // The kind catalog is fetched once, lazily: it only matters when the user
  // actually opens the attach menu.
  useEffect(() => {
    if (!attachMenuOpen || contextKinds.length) return;
    getContextKinds()
      .then((r) => setContextKinds(r.data || []))
      .catch(() => setContextKinds([]));
  }, [attachMenuOpen, contextKinds.length, setContextKinds]);

  const addReferences = useCallback((items) => {
    setAttachmentError('');
    setPendingReferences((prev) => {
      const seen = new Set(prev.map((r) => `${r.kind}:${r.id}`));
      const next = [...prev];
      for (const item of items || []) {
        const key = `${item.kind}:${item.id}`;
        if (seen.has(key)) continue;
        if (next.length >= MAX_REFERENCE_COUNT) {
          setAttachmentError(`Only ${MAX_REFERENCE_COUNT} attached entities are allowed per message.`);
          break;
        }
        seen.add(key);
        next.push(item);
      }
      return next;
    });
  }, [setAttachmentError, setPendingReferences]);

  const removeReference = useCallback((kind, id) => {
    setPendingReferences((prev) => prev.filter((r) => !(r.kind === kind && r.id === id)));
  }, [setPendingReferences]);


  // A file only reaches a workspace if one is selected; clearing the box
  // must also clear the promise attached to what is already in it.
  useEffect(() => {
    if (selectedWorkspace) return;
    setPendingAttachments((prev) => prev.map((a) => ({ ...a, store_to_workspace: false })));
  }, [selectedWorkspace, setPendingAttachments]);

  return {
    resizeTextarea, onPickFiles, removeAttachment, toggleAttachmentStore,
    addReferences, removeReference,
  };
}

export default useChatComposerInput;
