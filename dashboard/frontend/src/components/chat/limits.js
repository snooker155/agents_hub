// What the composer will carry in one turn.

export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024; // 5 MB
export const MAX_ATTACHMENT_COUNT = 6;
// Attached hub entities are resolved server-side and can each render into
// thousands of characters, so the composer caps them the way it caps files.
// Mirrors chat/references.py's MAX_REFERENCES.
export const MAX_REFERENCE_COUNT = 10;
