# Chat widget

A chat bubble for your own website. One script tag on a page gives its
visitors a chat with one agent of one workspace: conversations kept on the
hub, replies streamed as they are written, files attached from the visitor's
computer. The Widgets page (`/widgets`, in the sidebar) is where a widget is
created, embedded, previewed and read back.

The pieces: `widgets/` (the record, the checks, the turn), the routes in
`dashboard/backend/routes/widget.py`, the script in
`dashboard/frontend/widget/widget.js` (plain JavaScript, served as is), and
migration `0024_widgets` (tables `widgets`, `widget_threads`,
`widget_messages`).

## The snippet

```html
<script src="https://hub.example.com/widget.js"
        data-widget="wgt_3f2a9c0d1e2b4a5c"
        data-key="ahw_…"
        async></script>
```

Paste it before the closing `body` tag of every page that should show the
chat. The Embed tab shows it with the widget's own id and key, and the hub's
address taken from `AUTH_PUBLIC_URL` when that is set, else from the request
(honouring `X-Forwarded-Host` and `X-Forwarded-Proto`).

Optional attributes: `data-hub` (the hub's origin, when the script is served
from somewhere else; by default it is the script's own origin) and
`data-open="true"` (open the panel at once). The page can also drive it:
`window.AgentsHubWidget.open()`, `.close()` and `.toggle()`, each optionally
with a widget id, for a "Chat with us" button of your own.

`/widget.js` is cached for five minutes with an ETag, so a hub upgrade
reaches every site within minutes and an unchanged script costs a 304.

## What a widget is

| Field | Meaning |
|---|---|
| `widget_id` | `wgt_` and 16 hex characters. Named in the snippet. |
| `workspace`, `agent_id` | Where it runs and who answers. The agent must be one the workspace may run (the chat's own rule: system agents everywhere, an unshared agent only in its owner workspace, a workspace's `allowed_agents` list). Fixed workspace, changeable agent. |
| `owner_id` | Whose principal the widget acts as: the person who created it. Fixed. |
| `public_key` | `ahw_…`, the publishable key. Public by design, in the snippet. Rotatable. |
| `allowed_origins` | The exact origins that may embed it: `https://shop.example`, `https://shop.example:8443`. Plain `http` only for `localhost` and loopback addresses. No paths, no wildcards inside a host. A lone `*` (any site) is accepted only outside `multi` mode. |
| `enabled` | Off: the public side answers `widget_disabled` and the bubble is not drawn. |
| `title`, `greeting`, `placeholder` | The header, the first message a visitor sees, the input's placeholder. |
| `accent` | One of `navy`, `blue`, `teal`, `green`, `amber`, `rose`, `slate`. A name, never a colour: the script maps it to its own palette for light and dark. |
| `language` | `auto` (the visitor's browser), `en`, `ru` or `de`. The script carries its strings in all three. |
| `limits` | `messages_per_minute` per visitor (1 to 120, default 6), `attachment_max_bytes` (0 to 5 MB, default 2 MB, 0 turns attachments off), `max_attachments` per message (0 to 5, default 3), `tokens_per_day` for the whole widget (0 is no cap, default 200 000). |
| `agent_version` | A stored version of the agent (docs/agents.md "Versions and pinning") the visitors talk to; `null` is the live definition. Checked against the agent's history; changing the agent without naming a version clears it. An agent the thread was handed to answers as it is. |

## Security model

A visitor has no hub account and must not need one, so the public side,
`/api/widgets/public/{widget_id}/…`, is open in `common.auth.is_open_path`
and guarded by the widget itself (`widgets/service.py`), in this order:

1. **Per address.** At most 120 public requests a minute from one address,
   across widgets, before anything else is looked at.
2. **The publishable key.** `X-Widget-Key` must be the widget's key. It says
   which widget, not who: anyone can read it in your page source. What it
   buys you is rotation. Rotating it (Embed tab) cuts off every copy of the
   old snippet at once, and visitors keep their conversations.
3. **The origin.** `Origin` must be one of the allowed origins; a request
   with no Origin, or another one, is refused. A browser cannot lie about
   its Origin, so no other site can embed your widget. A script outside a
   browser can, which is what the limits are for.
4. **The visitor token.** Thread routes need `X-Visitor-Token`: an
   anonymous visitor id minted by the server inside an HMAC-signed token
   bound to this widget, valid 30 days and renewed (same id, new expiry)
   whenever the widget opens. The script keeps it in `localStorage`. Nothing
   is stored for a visitor on the hub; the signature is the proof. At most
   10 new visitors a minute per address.
5. **The limits.** Messages per visitor per minute, four times that per
   address (a household or an office behind one address), the size and
   number of attachments, and the widget's tokens for the day (summed from
   its turns since 00:00 UTC). Past a limit: `429` with `Retry-After`.
6. **The owner.** A turn runs as the widget's owner narrowed to the widget's
   workspace. In `multi` mode the owner must still exist and be an editor of
   the workspace (or an administrator), and the agent must still be allowed
   there: an owner who loses the workspace takes the widget down with them
   (`widget_unavailable`), and the Widgets page says so.

CORS for the public paths is answered per widget by
`widgets/edge.py`: the preflight and the response headers echo an allowed
origin, never with credentials. The hub's own `ALLOW_ORIGINS` setup for the
dashboard is not widened: on these paths its headers are replaced, on every
other path it works as before.

Refusals are `{"detail": "...", "code": "..."}` with a stable code:
`bad_key`, `origin_not_allowed`, `widget_disabled`, `widget_unavailable`,
`visitor_token_invalid`, `thread_not_found`, `rate_limited`, `daily_limit`,
`attachments_disabled`, `too_many_attachments`, `attachment_too_large`,
`message_too_long`, `empty_message`, `busy`, `too_many_threads`. The script
turns each into a sentence in the visitor's language.

The widget's visitor routes are not written to the audit log (they would
bury it); every change to a widget is, as `widget.create`, `widget.update`,
`widget.delete`, `widget.rotate_key` and `widget.thread_delete`.

## What the visitor sees, and what they do not

The turn is the web chat's own: `chat.pipelines.run_chat_pipeline`, the
thread as the conversation, `message_origin="widget"`. Its tools, memory,
guardrails and budget apply exactly as in the chat. What reaches the visitor
is a whitelist of events streamed back over the POST as SSE:

| Event | Fields |
|---|---|
| `meta` | `run_id`, `thread_id` |
| `token` | `token` |
| `tool_start`, `tool_end` | `tool`, the name only |
| `handoff` | `to_agent_name`, `from_response` (the first agent's reply) |
| `done` | `ok`, `response` and `citations` (`[{n, title, snippet, url?}]`) when ok, else `error`: `stopped` or `failed` |

Never: thinking, tool inputs and outputs, usage, entity links, stored file
paths, a delegated agent's events, provider error text, stack traces, the
workspace or the owner. Sources show as a numbered list under the reply for
the `[n]` markers in it; a source's pool, file and chunk ids stay on the hub.

A reply is drawn by the script's own small renderer (paragraphs, lists, code
blocks, inline code, bold, `http`/`https` links opening in a new tab with
`rel="noopener noreferrer"`); no text from the hub ever goes through
`innerHTML`. The whole widget lives in a closed Shadow DOM, so the page's
CSS cannot break it and its CSS cannot leak into the page.

The stop button aborts the request; the hub then stops the run the way the
chat's stop button does, and keeps the partial reply marked as stopped. A
visitor who closes the page mid-answer loses nothing either: the reply is
written to the thread whether anybody is still reading or not.

Attachments travel base64 in the message body, checked against the widget's
limits before the turn starts. A text file (by type or extension, valid
UTF-8) goes into the prompt; anything else is stored in the workspace for
the agent's file tools, as a chat upload is.

## Threads, for the owner

The Conversations tab lists every visitor's threads, newest first, with the
visitor's short id, the message count and two marks: *Preview* for threads
started from the live preview, *Deleted by the visitor* for threads a visitor
deleted in the widget (hidden from them, kept for you). A thread's transcript
shows each reply with its agent, its status, its tokens and a link to the run
behind it (`/messages/<run_id>`), where the whole turn (prompt, tools,
thinking, cost) is. Deleting a thread there is for good; its runs stay on the
Messages page.

A handoff (see [handoffs](handoffs.md)) moves the thread to the agent that
took over: later messages go to it for as long as it may run in the widget's
workspace, and both replies are kept with a divider between them.

## The live preview

The Preview tab loads a page with the widget embedded, running the real
script against the real public API. The page runs on the hub's own origin,
which is not one of the widget's, so it presents a preview ticket
(`POST /api/widgets/{id}/preview`, editors only, 30 minutes, bound to the
widget); with it the hub's own origins pass the origin check, and a widget
that is still switched off can be tried before it goes live.

## Routes

Management, normal hub auth (read: workspace visibility; write: editor):

| Route | Answer |
|---|---|
| `GET /api/widgets/options` | accents, languages, limit bounds, whether `*` is allowed |
| `GET /api/widgets?workspace=` | the workspace's widgets, with `thread_count`, `tokens_today`, `owner_ok` |
| `POST /api/widgets` | create |
| `GET`, `PATCH`, `DELETE /api/widgets/{id}` | read, change, delete (with its threads) |
| `POST /api/widgets/{id}/rotate-key` | a new publishable key |
| `GET /api/widgets/{id}/snippet` | `{snippet, script_url, hub}` |
| `POST /api/widgets/{id}/preview` | a preview ticket |
| `GET /api/widgets/{id}/threads` | every visitor's threads |
| `GET`, `DELETE /api/widgets/{id}/threads/{thread_id}` | a transcript; delete for good |

Public, guarded as above:

| Route | Answer |
|---|---|
| `GET /widget.js` (also `/api/widgets/public/widget.js`) | the script |
| `GET …/{id}/config` | title, greeting, placeholder, accent, language, agent name, attachment limits |
| `POST …/{id}/visitor` | `{visitor_token, visitor_id, expires_at}`; a renewal with a valid `X-Visitor-Token` |
| `GET`, `POST …/{id}/threads` | the visitor's threads; a new one |
| `GET`, `DELETE …/{id}/threads/{thread_id}` | a thread with its messages; hide it |
| `POST …/{id}/threads/{thread_id}/messages` | `{text, attachments: [{name, mime_type, data_b64}]}`, answered as SSE |

## Gotchas

- An origin is exact. `https://shop.example` does not cover
  `https://www.shop.example`; list both.
- A widget with no origins works nowhere (except in the preview).
- The limits live in the hub process: with several API replicas each keeps
  its own per-minute windows, like the hub's other rate limits. The daily
  token cap is read from the database and holds across replicas.
- One message at a time per thread: a second one while the first is being
  answered is `busy`.
- A visitor who clears their browser storage becomes a new visitor; their
  old threads stay visible to you.
