# Terminal

A shell in a running container, from the dashboard: the container of a run started in Docker, or the container of a service replica. For looking at what an agent left on disk, checking a package, reading a log the run wrote, or poking at a replica that answers oddly, without leaving the hub or reaching for `docker exec` on the host.

## Opening one

- **A run**: the run page (Messages, then the run) shows **Terminal** in its header while the run is working and has a container. A run in local mode runs in the hub's own process and has no container, so the button is not there; asked directly, the hub answers that the run executes in local mode.
- **A service replica**: on the service page, each replica row whose carrier is a container has a terminal button next to Stop.

The panel docks at the bottom of the page. It is a real terminal (xterm.js): colours, cursor movement, full-screen programs such as `top` or `vi`, copy and paste, and it follows the panel's size.

The shell is `bash -l` when the image has bash, else `sh -l`, started with `docker exec -it` in the container as its own user and working directory. Nothing about the container changes: a hardened run container keeps its read-only root, dropped capabilities and network posture ([containers](containers.md), [environments](environments.md)). The shell can see what the run sees, including its environment variables.

A finished run's container is removed with the run, so there is nothing to open: the hub says the run has finished. A replica that has stopped is the same. A container on another host (a run launched by a worker elsewhere, see [workers](workers.md)) is refused with the host's name, because only the hub process on that host talks to its Docker daemon.

## Reconnecting

The shell belongs to the hub, not to the browser tab. When the socket drops (a laptop lid, a proxy timeout, a flaky network) the hub keeps the shell for a grace period and keeps reading its output into a buffer. The panel reconnects on its own with growing pauses; when it gets back in time, the hub replays the recent output and the shell carries on, with whatever was running in it still running.

- **Hide** (the cross) only drops the socket. Opening the terminal again within the grace period, from the same tab or after a reload, resumes the same shell.
- **End session** closes the shell at once.
- Opening the same session in a second window moves it there; the first panel says so and offers a new session.

When the grace period passes with nobody attached, the shell is closed. A shell with no input and no output for the idle limit is closed too, attached or not.

## Who may open one

A shell reaches everything the run can, so the rule is strict:

- Outside `multi` mode, the one operator (or the shared token) may.
- In `multi` mode, an admin may. Otherwise the person must own the run (the chat's owner, the person who filed the task; for a replica, whoever deployed the service) and hold at least the editor role in its workspace. A run whose owner is not known is left to admins.
- An agent's own credential (the service principal a run's process carries) never may.
- A personal API key narrowed to some workspaces reaches only those.

Each account may hold a limited number of sessions at once, counting the ones waiting out their grace period.

Every open, resume and close is written to the [audit log](audit.md) as `terminal.open`, `terminal.resume` and `terminal.close`, with the container and the session id; the close row also says why it ended (`exited`, `closed`, `grace`, `idle`, `shutdown`), how long it lasted and how many bytes went each way. Keystrokes are not recorded.

## Settings

Read live from `.env` or the environment, no restart needed:

| Setting | Default | What it does |
| --- | --- | --- |
| `TERMINAL_GRACE_SECONDS` | 60 | How long a shell waits for a dropped socket to come back. |
| `TERMINAL_IDLE_SECONDS` | 900 | A shell with no input and no output this long is closed. |
| `TERMINAL_BUFFER_BYTES` | 262144 | How much recent output is kept for a reconnect to replay. |
| `TERMINAL_MAX_PER_USER` | 3 | Sessions one account may hold at once. |

## How it works

1. The panel calls `POST /api/terminal/{kind}/{id}/ticket` (`kind` is `run` or `replica`) with the ordinary credential. The hub checks that the target has a running container on this host and that the caller may open a shell in it, and answers a one-time ticket valid for a minute that names that target. A refusal comes back here with its reason. With `{"session_id": ...}` the ticket resumes that session, which must be the caller's.
2. The panel opens `GET /api/terminal/{kind}/{id}/ws?ticket=...&cols=...&rows=...`. The ticket is the only credential the socket accepts, in every mode, and it opens only the target it was minted for. A ticket is spent on first use, so each reconnect mints a new one.
3. The hub starts `docker exec -it <container> /bin/sh -c ...` on a pseudo-terminal of its own (`common/terminal.py`), so the Docker CLI forwards raw bytes both ways and resizes the container's terminal when the panel resizes.

Messages on the socket: the panel sends JSON text (`{"type": "input", "data": ...}`, `{"type": "resize", "cols": ..., "rows": ...}`, `{"type": "close"}` to end the session). The hub sends one `{"type": "session", ...}` first (the session id, whether it was a resume, the limits), then the replayed buffer and the live output as binary frames, and `{"type": "exit"}` when the shell ends, `{"type": "taken_over"}` when another window attached, or `{"type": "error", "detail": ...}` before refusing. `GET /api/terminal/sessions` lists your open sessions (an admin sees everyone's).

Behind a proxy the socket needs the upgrade forwarded. The dashboard image's nginx does this for `/api/terminal/.../ws` (and the browser's frame stream), and the Vite dev server proxies WebSockets under `/api`. Another reverse proxy needs the same: `Upgrade` and `Connection: upgrade` on those paths and a read timeout longer than the idle limit.

## Limits

- The hub must reach the container's Docker daemon: the host that started the run or the replica. A run on another worker host is refused rather than relayed.
- Sessions live in the hub process that opened them. With several API replicas behind a load balancer, a reconnect that lands on another replica cannot resume and starts a new shell; pin WebSockets to one replica (sticky sessions) if that matters.
- A restart of the hub closes every shell.
- Needs a POSIX host (Linux or macOS) for the pseudo-terminal.

Related: [containers](containers.md), [services](services.md), [sessions-and-runs](sessions-and-runs.md), [audit](audit.md), [identity](identity.md), [browser](browser.md).
