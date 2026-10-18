# Terminal

A shell in a running container, from the dashboard: the container of a run started in Docker. For looking at what an agent left on disk, checking a package or reading a log the run wrote, without leaving the hub or reaching for `docker exec` on the host.

## Opening one

The run page (Messages, then the run) shows **Terminal** in its header while the run is working and has a container. A run in local mode runs in the hub's own process and has no container, so the button is not there; asked directly, the hub answers that the run executes in local mode.

The panel docks at the bottom of the page. It is a real terminal (xterm.js): colours, cursor movement, full-screen programs such as `top` or `vi`, copy and paste, and it follows the panel's size.

The shell is `bash -l` when the image has bash, else `sh -l`, started with `docker exec -it` in the container as its own user and working directory. Nothing about the container changes: a hardened run container keeps its read-only root, dropped capabilities and network posture ([containers](containers.md)). The shell can see what the run sees, including its environment variables.

A finished run's container is removed with the run, so there is nothing to open: the hub says the run has finished.

## Reconnecting

The shell belongs to the hub, not to the browser tab. When the socket drops (a laptop lid, a proxy timeout, a flaky network) the hub keeps the shell for a grace period and keeps reading its output into a buffer. The panel reconnects on its own with growing pauses; when it gets back in time, the hub replays the recent output and the shell carries on, with whatever was running in it still running.

- **Hide** (the cross) only drops the socket. Opening the terminal again within the grace period, from the same tab or after a reload, resumes the same shell.
- **End session** closes the shell at once.
- Opening the same session in a second window moves it there; the first panel says so and offers a new session.

When the grace period passes with nobody attached, the shell is closed. A shell with no input and no output for the idle limit is closed too, attached or not.

## Settings

Read live from `.env` or the environment, no restart needed:

| Setting | Default | What it does |
| --- | --- | --- |
| `TERMINAL_GRACE_SECONDS` | 60 | How long a shell waits for a dropped socket to come back. |
| `TERMINAL_IDLE_SECONDS` | 900 | A shell with no input and no output this long is closed. |
| `TERMINAL_BUFFER_BYTES` | 262144 | How much recent output is kept for a reconnect to replay. |
| `TERMINAL_MAX_PER_USER` | 3 | Sessions that may be open at once. |

## How it works

1. The panel calls `POST /api/terminal/{kind}/{id}/ticket` (`kind` is `run`). The hub checks that the target has a running container, and answers a one-time ticket valid for a minute that names that target. A refusal comes back here with its reason. With `{"session_id": ...}` the ticket resumes that session.
2. The panel opens `GET /api/terminal/{kind}/{id}/ws?ticket=...&cols=...&rows=...`. The ticket is the only credential the socket accepts, and it opens only the target it was minted for. A ticket is spent on first use, so each reconnect mints a new one.
3. The hub starts `docker exec -it <container> /bin/sh -c ...` on a pseudo-terminal of its own (`common/terminal.py`), so the Docker CLI forwards raw bytes both ways and resizes the container's terminal when the panel resizes.

Messages on the socket: the panel sends JSON text (`{"type": "input", "data": ...}`, `{"type": "resize", "cols": ..., "rows": ...}`, `{"type": "close"}` to end the session). The hub sends one `{"type": "session", ...}` first (the session id, whether it was a resume, the limits), then the replayed buffer and the live output as binary frames, and `{"type": "exit"}` when the shell ends, `{"type": "taken_over"}` when another window attached, or `{"type": "error", "detail": ...}` before refusing. `GET /api/terminal/sessions` lists your open sessions.

Behind a proxy the socket needs the upgrade forwarded. The dashboard image's nginx does this for `/api/terminal/.../ws` (and the browser's frame stream), and the Vite dev server proxies WebSockets under `/api`. Another reverse proxy needs the same: `Upgrade` and `Connection: upgrade` on those paths and a read timeout longer than the idle limit.

## Limits

- The hub must reach the container's Docker daemon.
- A restart of the hub closes every shell.
- Needs a POSIX host (Linux or macOS) for the pseudo-terminal.

Related: [containers](containers.md), [sessions-and-runs](sessions-and-runs.md), [browser](browser.md).
