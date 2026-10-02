# Issue trackers

Jira and Linear, mirrored into a [project](projects.md)'s tasks the way GitHub
and GitLab issues are. A sync is idempotent: an issue maps to one task through its
external source (provider, remote id, number, key, URL, state, status, labels).
A closed issue closes a task only while it is todo or ready; a reopened issue
reopens a done task. Nothing else moves a task someone is working on.
Credentials are saved on the **Connectors** page, "Jira & Linear" tab.

## Jira

Fields: `base_url` (the site, e.g. `https://acme.atlassian.net`; a host
outside `*.atlassian.net` is checked against private addresses), `email` and
`api_token` (REST v3). Test calls `myself` and shows the display name.

## Linear

Field: `api_key` (GraphQL). Test asks for the viewer and shows the name.

## Linking a project

On the project page, **Repository** tab, "Issue tracker" card: pick Jira or
Linear and enter the project key or team key. A picker lists them from `GET
/api/trackers/{provider}/projects`. Save and click Sync. The sync is
idempotent: issues from the tracker become tasks on every sync.

API: `GET|PUT /api/trackers/projects/{id}` (`{provider, remote_id, url}`),
`POST /api/trackers/projects/{id}/sync`.

## Tools

- `tracker_list_issues(provider, remote_id, state="open", limit=50)` (reads
  private, ingests untrusted)
- `tracker_get_issue(provider, key)` (reads private, ingests untrusted)
- `tracker_sync(project)`: project id or unique name (reads private, ingests
  untrusted)
- `tracker_comment(provider, key, text)` (can exfiltrate)
- `tracker_transition(provider, key, status)` where status is matched against
  the tracker's own workflow states, case insensitive (can exfiltrate)
- `tracker_create_issue(provider, remote_id, title, body, labels)`: note that
  Linear create does not set labels (can exfiltrate)

Issue bodies returned to the agent are wrapped as untrusted text. The three
writing tools are classified as able to send data out; the reading ones as
ingesting untrusted text and reading private data.

## Gotchas

- **Closed means the status category, not the status name.** A Jira issue is
  closed when its status sits in the "Done" category, a Linear issue when its
  state type is completed or canceled, whatever the status is called. The
  tracker's own status name is kept on the task's external source.
- **Linear teams are not workspaces.** When you pick a team on the project
  page, the sync brings issues for that team only. Teams cannot see each
  other's issues.

Related: [projects](projects.md), [tasks](tasks.md), [tools-and-capabilities](tools-and-capabilities.md).
