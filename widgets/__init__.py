"""
The embeddable chat widget (docs/widget.md).

A site owner adds one tag, ``<script src="https://<hub>/widget.js"
data-widget="wgt_..." data-key="ahw_..." async></script>``, and visitors of
that site get a chat bubble that talks to one agent of one workspace, with
threads kept on the hub, streamed replies and attachments.

The pieces:

- :mod:`widgets.models`: the widget record and the validation of everything a
  site owner can set (origins, accent, language, limits).
- :mod:`widgets.store`: the three tables of migration 0024.
- :mod:`widgets.service`: create, update, rotate, snippet, and the checks a
  public request goes through (key, origin, visitor, limits).
- :mod:`widgets.visitor`: the anonymous visitor token, HMAC signed.
- :mod:`widgets.agents`: which agents a workspace may run, the same rule the
  chat's agent list applies. ``/v1`` uses it for its ``agent:<id>`` models.
- :mod:`widgets.relay`: a chat turn driven on a task of its own, so a caller
  that goes away stops the run instead of abandoning it. ``/v1`` uses it too.
- :mod:`widgets.turn`: one visitor message as a stream of visitor-safe events.
- :mod:`widgets.edge`: the ASGI middleware that serves ``/widget.js`` and
  answers CORS for the public paths per widget.

The routes are ``dashboard/backend/routes/widget.py``; the script is
``dashboard/frontend/widget/widget.js``.
"""
