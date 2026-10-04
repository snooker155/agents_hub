/**
 * A file shown the way a reader would see it, wherever a page previews one:
 * the Artifacts page (workspace files) and the Files tab of a project.
 *
 * The caller loads the file and passes what it has: `text` (with `kind`
 * 'text' | 'pdf' | 'binary', as the files and projects APIs return it) and,
 * for an image, a PDF or an HTML page, `url`, an object URL of its bytes.
 * An image is shown as an image, a PDF in the browser's viewer, an HTML page
 * in a sandboxed frame (it must never run against this page), Markdown
 * rendered, code highlighted the way the chat shows it, other text as is.
 * `source` switches a PDF to its extracted text and an HTML or Markdown file
 * to its source; `hasSourceView` (lib/fileKind.js) says when offering that
 * switch makes sense.
 */
import MarkdownRenderer from '../MarkdownRenderer';
import CodeBlock from '../CodeBlock';
import { codeLanguageFor } from '../../lib/codeLanguage';
import { useI18n } from '../../i18n';
import { isHtmlFile, isImageFile, isMarkdownFile, isPdfFile } from '../../lib/fileKind';

export default function FileViewer({
  name, mimeType, url = '', text = null, kind = 'text', truncated = false, source = false,
  frameClassName = 'w-full h-[75vh] rounded border border-gray-200 bg-white',
  imageClassName = 'max-w-full max-h-[75vh] mx-auto rounded',
  codeClassName = 'max-h-[75vh] overflow-auto',
}) {
  const { t } = useI18n();
  const file = { name, mimeType };
  let body;
  if (isImageFile(file) && url) {
    body = <img src={url} alt={name || ''} className={imageClassName} />;
  } else if ((isPdfFile(file) || isHtmlFile(file)) && url && !source) {
    // A browser's PDF viewer does not load inside a sandboxed frame; HTML is
    // the one that must never run against this page, so only it is sandboxed.
    body = <iframe src={url} title={name || ''} sandbox={isHtmlFile(file) ? '' : undefined} className={frameClassName} />;
  } else if (text != null && isMarkdownFile(file) && !source) {
    body = <MarkdownRenderer content={text} className="text-sm text-gray-800" />;
  } else if (text != null && kind !== 'pdf' && codeLanguageFor(name)
    && (source || !(isMarkdownFile(file) || isHtmlFile(file)))) {
    body = <CodeBlock language={codeLanguageFor(name)} code={text} bodyClassName={codeClassName} />;
  } else if (text != null) {
    body = (
      <>
        {kind === 'pdf' && <p className="text-[11px] text-gray-400 mb-1">{t('files.previewPdf')}</p>}
        <pre className="whitespace-pre-wrap break-words rounded bg-gray-50 p-3 text-xs text-gray-700">{text}</pre>
      </>
    );
  } else {
    body = <p className="text-sm text-gray-500">{t('files.previewBinary')}</p>;
  }
  return (
    <>
      {body}
      {truncated && text != null && <p className="text-[11px] text-gray-400 mt-2">{t('files.previewTruncated')}</p>}
    </>
  );
}
