## Building a document view

A `document` view is a paginated document or report: a markdown body plus an optional title and print CSS. It exports to PDF from the UI through the browser's print path.

- `document_set(markdown, title, css)` sets the whole body. Headings, lists, tables, code and links are all markdown; `css` is for page size, margins and print styles only.
- Write the document for its reader, in their language, with the material the caller gave you; do not pad it with generic phrasing.
- To change a passage, call `document_set` again with the full updated body (read it first with `view_get` on `spec.markdown`).
