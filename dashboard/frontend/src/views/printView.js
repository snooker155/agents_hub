// Print / PDF export — opens a clean, self-contained window with the rendered
// HTML plus a print stylesheet and triggers the browser's print dialog (Save as
// PDF). Used by the document and slides renderers. Styling is by element tag so
// it renders well without the app's Tailwind classes; the view's own `css` is
// appended.

const BASE_CSS = `
  @page { margin: 20mm; }
  * { box-sizing: border-box; }
  body { font-family: Georgia, 'Times New Roman', serif; color: #111; line-height: 1.55;
         max-width: 820px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 26px; margin: 0 0 12px; } h2 { font-size: 20px; margin: 20px 0 8px; }
  h3 { font-size: 16px; margin: 16px 0 6px; }
  p { margin: 0 0 10px; } ul, ol { margin: 0 0 10px 22px; } li { margin: 2px 0; }
  code { font-family: ui-monospace, Menlo, monospace; background: #f4f4f5; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }
  pre { background: #1e293b; color: #e2e8f0; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; }
  pre code { background: none; color: inherit; padding: 0; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 13px; }
  th, td { border: 1px solid #d4d4d8; padding: 6px 10px; text-align: left; }
  th { background: #f4f4f5; }
  blockquote { border-left: 3px solid #d4d4d8; margin: 10px 0; padding-left: 12px; color: #52525b; }
  .slide { page-break-after: always; min-height: 90vh; padding-bottom: 24px; }
  .slide:last-child { page-break-after: auto; }
  hr { border: none; border-top: 1px solid #e4e4e7; margin: 16px 0; }
`;

export function printHtml(title, innerHtml, extraCss = '') {
  const w = window.open('', '_blank', 'width=900,height=700');
  if (!w) { alert('Allow pop-ups to export as PDF.'); return; }
  w.document.open();
  w.document.write(
    `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title || 'Document')}</title>`
    + `<style>${BASE_CSS}\n${extraCss || ''}</style></head><body>${innerHtml}</body></html>`,
  );
  w.document.close();
  w.focus();
  // give the new document a tick to lay out before printing
  setTimeout(() => { try { w.print(); } catch { /* noop */ } }, 350);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
