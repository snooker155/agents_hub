# The browser

The agent's browser on screen: a live view of a run's browser session with the option to take control, and a free browsing page whose session can be handed to an agent. Built on the same browser service the browser tools use, under the same domain policy.

Everything here needs the browser service (`deploy/browser/`) and the two settings `AGENTS_HUB_BROWSER_URL` and `AGENTS_HUB_BROWSER_TOKEN`; see [Tools and capabilities](tools-and-capabilities.md), section "The browser tools", for how to start it. Without them the Browser page says so and links here, and a run's live output shows no browser panel.

## Watching a run's browser

A run that calls any `browser_*` tool gets a **Browser** panel in its live output (the task page, and everywhere else the live output appears), under the list of tool calls. The panel shows the page the run's session is on, pushed as the page paints: the hub relays the service's frame stream (Chromium's own screencast over CDP, JPEG frames of the viewport, one per paint and nothing while the page is still) over a WebSocket at `/api/browser/sessions/{id}/ws`, opened with a one-time ticket minted just before each connect. When that socket cannot be opened, or the service has no CDP for the page, the panel polls `/frame` a little faster than once a second instead; the service itself falls back to a screenshot every half second, sent only when the picture changed. The picture pauses while the tab is hidden and a dropped stream reconnects after three seconds. A finished run shows the page where it was left, once, without polling.

The hub finds the session through the browser service, which records for every session the run it serves, its workspace, whether an agent or a person drives it, and a label (the agent or the person's name). The panel asks once the run's first browser tool appears, and asks again on the next browser tool if the first answer was "no session yet".

## Taking control

**Take control** turns the picture into an input surface on the same session the agent's `browser_act` works in. Clicks are mapped from the scaled picture back to the 1280x800 viewport, typed characters are sent in short batches, Enter, Backspace, Tab, Escape and the arrow keys are sent as key presses, and the mouse wheel scrolls the page. The toolbar adds back, reload and an address bar. **Release** hands the page back to watching.

The session has one lock. A click that arrives while the agent is in the middle of an action waits for it, so a person and an agent never interleave inside one step.

Taking control also pauses the agent at its next browser step. The dashboard registers the hold with the service (`POST /sessions/{id}/control`), and while it is held the service answers the agent's `navigate` and `act` with 423; `browser_open` and `browser_act` then wait, polling the session every two seconds, until the hold is released, and only then go on. `browser_read` and `browser_screenshot` keep working, so the agent can see what the person is doing. The wait is bounded by `AGENTS_HUB_BROWSER_CONTROL_WAIT` (300 seconds by default): past it the tool tells the agent who has the page and suggests reading it and trying later. The hold is released by **Release**, by leaving the page, and on its own after `BROWSER_CONTROL_IDLE` seconds (120) without a frame or an input from the person, so a closed tab never parks a run for good. Taking control is for getting past something a model should not do (a login, a captcha, a consent banner).

What a person does is subject to exactly what the agent is:

- An address typed in the toolbar is checked on the hub with the same `validate_url` `browser_open` uses (scheme, the workspace's domain policy, the private-network block) under the session's workspace, then checked again by the service before the page moves.
- Every request the page makes after a click is filtered by the service with the session's policy, as for the agent.
- After every input the address the page landed on is checked again. A refused landing blanks the page and answers 403 with the reason, which the panel shows under the picture.

## The Browser page

**Browser** in the navigation is free browsing on the same service. Choose a workspace, then either type an address and press Enter or press **New session**. The session is created under that workspace's domain policy, owned by you and labelled with your user name; its page is yours to drive at once.

The list on the left shows the workspace's open sessions, the agents' and the people's, with a badge for each. Opening an agent's session shows it the way a run's panel does, with the same Take control toggle. The close button ends the session on the service.

Sessions close on their own after `BROWSER_IDLE_TIMEOUT` seconds without use. Watching counts as use: every frame touches the session.

## Handing a page to an agent

**Hand to agent** on an open session asks for an agent of the workspace and a message, then:

1. creates a task in the session's workspace with the message as its description and a note that a browser session is attached (the session id and the page it is on);
2. assigns the agent and starts it, on the same terms as assigning from the task board (the workspace's allowed agents, node or subprocess mode);
3. retags the session on the service: owner `agent`, the agent as label, and the new run's id.

The run's browser tools then continue in that page, with the cookies and whatever the person already did in it. `tools/browser.py` looks for a handed over session before opening a new one: the `adopt_session` context variable (in process), the `AGENTS_HUB_BROWSER_SESSION` environment variable (the hand-off sets it around the launch through `runtime.entity_launch.child_env`), then a session on the service tagged with the run's id. Each is checked once; a session that expired in the meantime is ignored and the run opens its own. The page shows a link to the task, where the run's live output carries the Browser panel as usual.

A session can only be handed to an agent in its own workspace, since its domain policy is that workspace's.

## A live check

`scripts/smoke_browser_service.py` drives a running service with a real
Chromium through the same steps: open a session, navigate, read, receive a
frame over the WebSocket screencast, take control as a person, see the agent's
navigate answered with 423 while reading stays open, send an input that
refreshes the hold, wait for the hold to lapse after `BROWSER_CONTROL_IDLE`
seconds, release, close. Start the service with a short idle window
(`BROWSER_CONTROL_IDLE=3`) and pass `--control-idle 3` so the expiry step is
quick. Last passed 2026-09-24 on the host with Playwright 1.49.

## Access

In `AUTH_MODE=multi` a session belongs to its workspace. A member of the workspace sees it in the list, its frames and its run's panel. Driving it, closing it and handing it over need the editor role there. The session list only ever shows sessions of workspaces the caller can see.

## API

All routes are under `/api/browser` and answer 503 with an explanation when the service is not configured.

| Route | What it does |
|---|---|
| `GET /status` | `{configured, url}` |
| `GET /sessions?workspace=` | `{sessions: [{session_id, run_id, workspace, owner, label, url, title, created_at, last_used_at}]}` |
| `POST /sessions` | `{workspace, url?}`: a session a person drives. Returns `{session_id, url, title}`. A refused first address is a 400 and opens nothing |
| `GET /sessions/{id}` | one session |
| `DELETE /sessions/{id}` | close it |
| `GET /sessions/{id}/frame` | `{url, title, width, height, image, controlled_by}`, `image` a JPEG data URL of the viewport |
| `WS /sessions/{id}/ws` | the frame stream: JSON messages of the `/frame` shape with `type: frame` and a `seq`, `type: keepalive` on a quiet page, one `type: error` before the close when the service cannot be reached. The credential is a one-time `?ticket=` from `POST /api/auth/ticket`, as for the SSE stream (see [identity](identity.md#tickets-for-streams)); `?token=` works only outside `multi` mode |
| `POST /sessions/{id}/control` | `{on}`: take or release control in the caller's name. Returns the session, with `controlled_by` |
| `POST /sessions/{id}/input` | `{kind, x, y, text, key, dx, dy, url}`; `kind` is one of click, dblclick, mousemove, type, key, scroll, navigate, back, forward, reload; coordinates are viewport pixels. Returns `{url, title, blocked}` |
| `GET /runs/{run_id}/session` | the session of a run, or 404 |
| `POST /sessions/{id}/handoff` | `{agent_id, message, workspace?}`: returns `{task_id, run_id}` |

The service behind it (`deploy/browser/app.py`) adds `GET /sessions`, `GET` and `PATCH /sessions/{id}`, `GET /sessions/{id}/frame`, the WebSocket `/sessions/{id}/stream`, `POST /sessions/{id}/input` and `POST /sessions/{id}/control` to the endpoints the agent's tools use. Its frames are JPEG at quality 60 of the fixed 1280x800 viewport; the dashboard scales them. `BROWSER_STREAM_FALLBACK_MS` (500) and `BROWSER_STREAM_KEEPALIVE` (15) tune the fallback and the keepalive.
