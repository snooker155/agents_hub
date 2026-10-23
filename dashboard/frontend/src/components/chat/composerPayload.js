// What a message box sends for what it carries besides its text, the same
// from every box (the Chat page builds its own in send/buildRequest.js).

/** What a turn sends for its attachments: a file by id, or its text. */
export function attachmentsPayload(attachments, workspace) {
  return attachments.map((a) => ({
    filename: a.filename,
    content: a.file_id ? '' : a.content,
    store_to_workspace: Boolean(a.store_to_workspace && workspace),
    ...(a.file_id ? { file_id: a.file_id } : {}),
  }));
}

/** What a turn sends for its attached hub records. */
export function referencesPayload(references) {
  return references.map((r) => ({ kind: r.kind, id: r.id, label: r.label || '' }));
}
