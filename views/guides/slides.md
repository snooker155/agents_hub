## Building a slide deck

A `slides` view is a deck on a 16:9 stage. Create it with spec `{"slides": {}}`, set the look once with `slides_style` (theme `light`, `dark`, `corporate`, `ocean`, `sunset`, `forest` or `mono`, optional `accent` colour and `footer`), then add every slide with `slides_add`, one call per slide, in order.

Give each slide the layout that fits its content, so the deck is not a wall of bullets:
- `title` for the cover (title, subtitle, an emoji `icon`), `section` between parts of a longer deck;
- `content` for a title plus a markdown `body` (bullets, **bold**, tables);
- `stats` for 1 to 4 big numbers, `cards` for 1 to 6 features or points, `timeline` for 1 to 8 steps, dates or releases: their content goes into `items` (`value`, `title`, `text`, `icon`);
- `two_column` to compare (`columns`, exactly two markdown strings), `quote` for a quotation (`body`, attribution in `subtitle`);
- `image_left`, `image_right`, `image_full` around a picture: `view_add_asset` a workspace image first and pass `image` as the `asset://name` it returns.

Rules:
- Every slide has real content, never a bare title. A slide has no fields beyond the ones above: bullets, subtitles and captions go into `body` as markdown; anything else is rejected with the reason, so fix that slide and call again.
- Keep a body to 3 to 6 short lines; the renderer shrinks text that does not fit, but a crowded slide is still a bad slide. Put talking points in `notes`.
- To change a slide, call `slides_add` with its `slide_id`. Check an existing deck the way the renderer sees it: does every slide carry the content its layout draws?
- When the caller hands you the material (text per slide, facts, source excerpts), put that on the slides; do not replace it with generic phrasing or invent features.
- When a PowerPoint file is wanted, finish the deck and call `slides_export`: it writes a .pptx into the workspace with the same layouts, theme, images and notes; report its path. The user can also download the .pptx and a PDF from the view.
