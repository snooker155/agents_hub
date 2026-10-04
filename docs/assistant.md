# Assistant

The assistant is one agent through which a person uses the whole service:
asks what is new, creates tasks, starts runs, checks spend, connects a
service, without opening the other pages. It is the `assistant` system agent,
which `extends` the Main Agent ([agent-inheritance](agent-inheritance.md)): it
has the Main Agent's tools and instructions, plus its own rules for answering
and, in an administrator's service thread, the hub's health tools. A turn is an
ordinary chat turn: tool policies, budgets, approvals and the audit trail all
apply. Voice (speech in, speech out) is built on top of the same turn.

## One thread per person

`GET`, `DELETE` and `POST /api/assistant`, and `POST /api/assistant/stop`:
the transcript, a fresh thread, one turn (a server sent event stream, like
every chat) and stopping a running turn. The thread is keyed on the person,
`("assistant", "user-<id>")`, so it follows them across pages and devices and
nobody else can read it, its archive included (`/api/entity-chats/sessions`
answers 404 for someone else's key).

The thread's session and the person's memory live in their **home
workspace**: their [personal workspace](identity.md#personal-workspace) in
`multi` mode, `default` otherwise. Personal memory is on for the assistant by
default in every workspace, like for the Main Agent, and the assistant always
uses the pool of the home workspace, wherever a turn runs.

A turn's body: `message`, `workspace` (where the turn runs; the home when
empty), `mode` (`personal` or `service`), `references` (hub records to attach,
as in the page chat; a record of another workspace is dropped) and `voice`
(whether the message was spoken). The `GET` answer adds `home`, `mode`,
`workspaces` (where this person can run a turn) and `service_available`.

## Where a turn runs

Every turn runs in one workspace: its run is filed there, its tools are
pinned there like any agent's ([workspaces](workspaces.md)), and files it
writes land there. The workspace must exist (404 otherwise) and the person must
be able to see it (403 otherwise); another person's personal workspace is
refused even to an administrator. So the assistant reaches exactly the
workspaces the person belongs to, one turn at a time, and "switch to the
sales workspace" is the next turn sent with `workspace: "sales"`.

The turn's prompt tells the agent who is speaking, where the turn runs, the
home workspace, the workspaces the person can reach, whether the message was
typed or spoken, and the hub's state in that workspace (the same snapshot the
[Help panel](help.md) reads).

## The service thread

In `multi` mode an administrator also has a service thread: `mode: service`,
keyed `"service-<id>"`, living in `default`. Only there, and only when the
turn runs in `default`, does the assistant hold the service tools
(`common/workspace_scope.py` `ASSISTANT_SERVICE_TOOLS`: `service_health`,
`run_diagnostics`, `list_sessions`, `routing_log`, `costs_summary`,
`list_instances`, `list_containers`, and stopping or restarting a run, an
instance or a container) and the workspace management tools. Run, error and
container logs are not among them: they carry text anyone could have written,
the assistant can also send messages, and that combination is what the
capability guard refuses; the Service Agent reads logs. A member asking for
the service thread gets 403.

In `single` and `token` mode there is one operator and one thread, in
`default`, and it is the service thread.

## Answers

The assistant opens every answer with one or two sentences that work read
aloud, then gives details and links to the page where the person can see the
thing. Before anything that costs money it names the cost and asks a yes or
no question. It never asks for a secret: a connection goes through
`propose_connection`, whose card collects the secret.

Approval cards and connection cards reach the thread's stream like in the
Chat page (`tool_approval` events), and the person whose turn it is answers
them.

## Limits

A turn is refused before it starts, with 402 and
`{"detail": {"code": "budget", "message": ...}}`, when the person's monthly
limit ([costs](costs.md#limit-per-person)) or the workspace's hard budget is
used up. Every turn is a run stamped with the person, so it appears in
Messages and counts toward their limit.
