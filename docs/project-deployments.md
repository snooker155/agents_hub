# Project deployments

A project's frontend and backend, run from inside the hub: deployed with one
button (or one tool call), watched, restarted when they die, shown inside the
dashboard, opened from outside through a share link, and opened by the agent's
own browser so it can test what it built. The Deploy tab of a
[project](projects.md) is where this lives; the Deployments page lists every
deployed app of the workspace.

## What a deployment is

One record per project (`deployments/models.py`, the `project_deployments`
document collection): the **mode**, the **services**, an optional
[environment](environments.md) for container limits, shared variables, and
what the runner last saw for every service. Three modes:

- **docker** (default): one container per service on the hub's `agents-hub`
  network. A service with a `Dockerfile` is built from it; one without runs in
  a stock image for its language (`node:20`, `python:3.12`) with its folder
  mounted at `/app` and its install and start commands run inside
  (`sh -lc "<install> && <command>"`). Every container publishes its port to a
  free port on the hub's host, loopback only on a host-run hub.
- **compose**: the project's own `docker-compose.yml` brought up as a compose
  project named after the deployment (`ah-app-<id>`), with `--build` on a
  deploy or rebuild and without on a restart. The services listed on the
  deployment are the ones the hub proxies to; their host ports are whatever
  the compose file publishes.
- **local**: plain subprocesses on the hub's host, no isolation, output to
  `.agents_hub/deployments/<id>/<service>.log`. For a developer's own
  machine, or a host without docker.

A **service** is one process that listens on a port: a name, a kind
(frontend, backend, other), a folder inside the project, a language, the
port it listens on, its install and start commands, an optional Dockerfile
or image, a health path and its own variables. Every service gets `PORT`,
`HOST=0.0.0.0` and `AGENTS_HUB_PUBLIC_PATH` (the `/apps/<slug>/` prefix the
app is served under) in its environment, on top of the deployment's and its
own variables.

## Detecting the services

The first time a project's Deploy tab opens (or `deploy_project` is called),
the services are proposed from the project folder, the cloned repository
when the project has one (`deployments/detect.py`):

- a `docker-compose.yml` (or `compose.yaml`) makes a compose deployment
  listing every service that publishes a port;
- a `package.json` makes a node service: Vite projects on 5173 with
  `npm run dev -- --host 0.0.0.0 --port $PORT`, Next.js on 3000, otherwise the
  `dev` or `start` script; `npm ci` when there is a lockfile, pnpm and yarn
  are recognised by their lockfiles;
- a Python entrypoint (`main.py`, `app.py`, `manage.py`, ...) makes a python
  service on 8000: `uvicorn` for FastAPI, `flask run` for Flask, `manage.py
  runserver` for Django, `python <file>` otherwise, with `pip install -r
  requirements.txt` first;
- a bare `index.html` is served as a static site; a bare `Dockerfile` is a
  docker service on its `EXPOSE` port;
- the conventional subfolders `frontend`, `backend`, `client`, `server`,
  `web`, `api`, `app`, `ui`, `site` are looked into one level down, so a repo
  with `frontend/` and `backend/` yields two services.

**Detect** proposes again from scratch (refused while the deployment runs);
**Configure** edits everything by hand. Dev servers bind to localhost by
default, which inside a container means unreachable: a command you write
yourself must pass the host (`--host 0.0.0.0`) where the tool needs it.

## Deploying and watching

**Deploy** stops what runs, builds what has a Dockerfile, starts every
service and answers at once; the status moves through `building` and
`starting` while the page polls. A service is `running` once its port
accepts a connection and its health path (default `/`) answers anything
below 500; until then it is `starting`, for at most
`AGENTS_HUB_DEPLOY_STARTUP_GRACE` seconds (90). The deployment is `running`
when every service is, `unhealthy` (shown as "Not responding") when one is
not, `failed` when none started. A service that is not answering carries the
reason next to it (`health_error`: nothing listens on the port, the health
path answered 5xx). In local mode a command that ignores `$PORT` and takes a
port of its own is found anyway: the hub looks at the ports the process
group listens on, adopts the real one, journals `port_adopted` and proxies to
it. In docker and compose modes the published port is fixed, so the command
has to listen on the configured port and on `0.0.0.0`. **Restart** does the same without rebuilding images; **Rebuild** is
Deploy. **Stop** takes everything down; the configuration and the share link
stay.

The **supervisor** (`deployments/supervisor.py`, on the
`project_deployments` lease, every `AGENTS_HUB_DEPLOYMENTS_TICK_SECONDS`
seconds, 15 by default) refreshes every deployment whose desired state is
running and, with "restart a service that exits on its own" on (the
default), starts an exited service again. Three restarts within five
minutes pause the deployment with the reason `crash loop`; fix the cause
and deploy again. Everything that happens is in the **journal** on the tab
(`GET /api/projects/{id}/deployment/events`): deploys, builds, starts,
failures, restarts, link changes, who did what.

**Logs** are the service's own output (`docker logs`, `docker compose
logs`, or the local log file), tailed on the tab with follow, and `build`
holds the image build output.

## Seeing the app

Three addresses, all through the hub, none straight to the container:

- **Inside the dashboard**: the Deploy tab shows the primary service (the
  frontend, or the one named as primary) in the same sandboxed iframe the
  Preview tab uses, through the ticket proxy (kind `deployment`, see
  [containers](containers.md), "Preview through the hub"). **Show** on
  another service switches the frame to it.
- **From outside**: `/apps/<slug>/`, an open path on the hub. While the link
  is **private** (the default) it needs the deployment's share key, once as
  `?key=...`, after which a cookie scoped to `/apps/<slug>` carries it; the
  key is stripped from what the app sees. **Make public** drops the key;
  **Reset link** replaces both the key and the slug, so every link handed
  out stops working. Set `AGENTS_HUB_PUBLIC_URL` and the tab shows the link
  absolute. A service other than the primary is at `/apps/<slug>/~<service>/`.
- **In the agent's browser**: the same `/apps/<slug>/` link on the origin the
  browser service reaches the hub at (`AGENTS_HUB_BROWSER_HUB_URL`, else the
  public URL, else `host.docker.internal:8000` for a browser service in a
  container and `localhost:8000` otherwise; `common/hub_urls.py`). **Open in
  the agent browser** on the tab opens it on the [Browser page](browser.md),
  and `project_deployment_status` returns it as `browser_url` for
  `browser_open`.

Why the last one needs a word: the browser service and the hub's
`validate_url` refuse every loopback and private address, which is exactly
where the hub itself lives from their point of view. The exemption is
narrow: only the origins the hub declares as its own (the public URL, the
browser's URL, `AGENTS_HUB_INTERNAL_ORIGINS`), and only the paths that serve
apps and previews (`/apps/`, `/preview/`). The hub's API and dashboard on
the same origin stay blocked, and the deny list still applies.

The proxy rewrites an app's absolute links and its redirects to stay under
the prefix and injects a `<base>` tag; an absolute URL built at runtime by
JavaScript is not rewritten (the known limitation of the preview proxy).
An app that reads `AGENTS_HUB_PUBLIC_PATH` (Vite's `base`, Next.js's
`basePath`, a router's basename) is served cleanly.

## The agent's tools

Under the `project_management` category (docs, [tools and
capabilities](tools-and-capabilities.md)):

- `deploy_project(project, services?, mode?, rebuild?, wait_seconds?)`:
  configure (optionally) and deploy, wait for the services to come up, and
  answer with the status, every service's state and the `browser_url`.
- `project_deployment_status(project)`: the same picture on demand.
- `project_deployment_logs(project, service?, tail?)`: a service's or the
  build's output.
- `stop_project_deployment(project)`.

`deploy_project` and `stop_project_deployment` are in the approval list
(`tools/approval.py`): the first runs the project's own commands, the second
takes something running away.

## API

Under `/api/projects/{project_id}/deployment`: `GET` (with `?refresh=false`
to skip asking the runner), `PUT` (mode, compose_file, services,
environment_id, env, primary_service, restart_on_exit), `POST .../detect`,
`POST .../deploy` (`?build=false` to skip image builds), `POST .../restart`,
`POST .../stop`, `DELETE` (stop and forget), `GET .../logs?service=&tail=`,
`GET .../events`, `PUT .../visibility` (`{visibility: private|public}`),
`POST .../link/reset`. `GET /api/deployments/apps?workspace=` lists the
workspace's deployments. Writes need the editor role in the project's
workspace; reads need the workspace to be visible.

## Settings

- `AGENTS_HUB_PUBLIC_URL`: where people reach the hub; makes the share link
  absolute.
- `AGENTS_HUB_BROWSER_HUB_URL`: where the browser service reaches the hub.
- `AGENTS_HUB_INTERNAL_ORIGINS`: more origins that are the hub itself, comma
  separated.
- `AGENTS_HUB_DEPLOY_BUILD_TIMEOUT` (900 s), `AGENTS_HUB_DEPLOY_STARTUP_GRACE`
  (90 s), `AGENTS_HUB_DEPLOYMENTS_TICK_SECONDS` (15 s).

## Gotchas

- Both `/preview/` and `/apps/` are the backend's paths outside `/api`. The
  Vite dev server (`vite.config.js`) and the dashboard image's nginx
  (`dashboard/frontend/docker/nginx.conf`) forward them; a reverse proxy of
  your own in front of the hub has to do the same, or the dashboard's
  index.html answers them and the frame stays blank.

- Docker mode mounts the project folder into a stock image, so `npm
  install` writes `node_modules` into the project folder on the host. A
  Dockerfile avoids that.
- A compose file that publishes no port for a service leaves the hub nothing
  to proxy to; the service is marked failed with that reason.
- Local mode runs the commands as the hub's own user on the hub's host, with
  the provider keys scrubbed from the environment (`tools/shell.py`) but
  nothing else between the app and the machine.
- Deleting a project does not stop its deployment; stop it first, or `DELETE`
  the deployment.

Related: [projects](projects.md), [containers](containers.md),
[browser](browser.md), [environments](environments.md),
[deployments](deployments.md) (scheduled jobs, a different thing with a
similar name).
