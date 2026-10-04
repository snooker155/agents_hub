/**
 * What kind of file a name and MIME type describe, for the file viewer
 * (components/files/FileViewer.jsx) and the pages that load a file for it.
 */
const ext = (name) => String(name || '').toLowerCase().split('.').pop();

export const isImageFile = ({ name, mimeType }) =>
  /^image\/(png|jpe?g|gif|webp|bmp)$/.test(mimeType || '')
  || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(ext(name));
export const isPdfFile = ({ name, mimeType }) => mimeType === 'application/pdf' || ext(name) === 'pdf';
export const isHtmlFile = ({ name, mimeType }) => mimeType === 'text/html' || ['html', 'htm'].includes(ext(name));
export const isMarkdownFile = ({ name, mimeType }) =>
  mimeType === 'text/markdown' || ['md', 'markdown'].includes(ext(name));

/** Whether the viewer needs the file's bytes (`url`) rather than its text. */
export const needsBytes = (file) => isImageFile(file) || isPdfFile(file) || isHtmlFile(file);

/** Whether the file has a rendered and a source view to switch between. */
export const hasSourceView = (file) => isPdfFile(file) || isHtmlFile(file) || isMarkdownFile(file);

/** An object URL for a Blob, typed so a frame renders it as `type`. */
export const objectUrl = (blob, type) => {
  if (typeof URL.createObjectURL !== 'function') return '';
  return URL.createObjectURL(type && blob.type !== type ? new Blob([blob], { type }) : blob);
};

/** The type to give a blob of `file` before it goes into a frame: the
 * server sends HTML as text/plain so it can never run as this origin. */
export const frameType = (file) => (isPdfFile(file) ? 'application/pdf' : isHtmlFile(file) ? 'text/html' : '');
