## Building a markdown view

A `markdown` view is one rendered markdown text at `spec.markdown`: notes, a summary, a checklist. For a paginated, printable report use a `document` view instead.

- Set the text with `view_apply_ops`: `{"op":"update","path":"spec.markdown","value":"# Title\n..."}`.
- Headings, lists, tables, code and links all render. Write it for its reader, in their language.
