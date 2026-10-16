# Sync between hubs

A hub linked to another ([hub link](hub-link.md)) can do more than borrow its
models and agents. Three modes, chosen on Settings, Remote hub:

| Mode | What leaves your hub | What the linked hub can read | Where agents run |
| --- | --- | --- | --- |
| **Catalog only** | Nothing | Nothing of yours | Here, with your keys |
| **Sync** | Everything you create in the chosen workspaces | All of it, and shows it as pages | Here and there |
| **Sync, end-to-end encrypted** | The same, sealed with your passphrase | Nothing: only sizes and dates | Here; there it is stored, not run |

The catalog lets you browse the linked hub's agents, skills, flows, teams
and loops and install copies here ([hub link](hub-link.md), "Catalog mode").
The rest of this page is about sync.

## What travels

Every kind of data a person creates, per workspace you map to a space (a
workspace of the same or another name) on the linked hub:

| Kind | Notes |
| --- | --- |
| Chats, assistant and help threads | Whole conversations |
| Memory | Pools and notes; your personal pool meets your personal pool there |
| Agents | Definition and prompts; secret names only, never values |
| Skills | Your own and agent-written ones (not those from git sources); the safety review runs again on arrival |
| Files | Up to the per-file limit (25 MB by default); larger ones are listed, not copied |
| Views | Spec and assets up to 10 MB |
| Flows, teams, loops, scenarios, worlds | Definitions |
| Tasks | With status, activity and results |
| Plans, watchers | Arrive paused or disabled, so two hubs never both fire them |
| Projects | Without deployments (those are live infrastructure on one hub) |
| Runs and flow, loop, team and scenario runs | Finished ones, as history; logs up to 1 MB |

Derived data does not travel and is rebuilt where needed: search indexes,
embeddings, caches.

## How it decides

A pass runs every 30 seconds, and on Sync now. It pulls what changed on the
linked hub, then pushes what changed here. Changes are found by content: each
record's portable form is hashed and compared with what was last exchanged,
so a store with no modification time still syncs, and a deleted record is
noticed by its absence.

When both sides changed the same record since the last exchange, the newer
modification wins and the other version is kept under Conflicts, where you
can read it and dismiss it. Nothing is overwritten without a trace. Kinds
that keep no modification time of their own (agents, files, assistant
threads) are timed by when this hub first noticed the edit, including while
the linked hub was out of reach, so an edit made offline on Monday still
beats one made there on Sunday.

A record that exists on both sides but was never exchanged (your personal
memory pool, met on a second device) is merged when both can be kept:
notes, blocks and files of both pools end up in one. When they cannot be
merged, the version that already travels wins and this hub's copy is kept
under Conflicts. A newly created, empty copy never wins just for being newer.

A record this hub cannot take (an agent id another workspace already uses,
a base agent that others still extend) is reported and left alone; it is
neither retried every pass nor mistaken for a deletion, and an edit you make
to it here still travels. A record that fails for any other reason does not
hold up the rest: the pass goes on and reports it. When a kind cannot be
listed in a pass (its store is briefly unreadable), no deletion of that kind
goes out in that pass.

Whose records a pass syncs is fixed when sync is first turned on: the person
who set it up (on a hub with one operator, that operator). Sync now and the
background pass sync the same records, whoever clicks.

**Tasks run where they were created.** A task arrives with its status, but
the hub it came from is the one that runs it; the runs and results come back
by sync. Otherwise a task ready on your laptop would also start on the
linked hub, twice the work on two sets of keys.

Turning sync on turns the older one-way mirror off as a whole (runs, chats
and memory), and it stays off while sync is on, even when the Remote hub form
is saved again: a chat or a run never travels twice, and nothing of an
encrypted workspace reaches the linked hub in clear.

A synced flow arrives owned by the workspace it was synced into, never as a
global flow: a global flow shows in every workspace without a flow list, so
it would surface in workspaces that never synced it. A workspace with a flow
list gets it added to that list. A flow deleted or unlisted on the other side
leaves the synced workspace only, and is deleted from this hub only when no
other workspace can see it: a global flow stays, and so does one another
workspace owns or lists. A flow that already exists here under the same id
but belongs to another workspace, or is global and not listed here, is
reported and left as it is.

An upload changed on the other side is a new file here, and the old one is
removed only once the new one is in place. When the new bytes equal another
file this workspace already syncs, the change is kept under Conflicts and the
old upload stays as it was, rather than being sent back as deleted.

A watcher that signs in with Google arrives only when it may: in a workspace
with no Google connector of its own it would read the hub operator's
mailbox, which only an administrator may set up, the same rule the Watchers
page applies.

## End-to-end encryption

With encryption on, each record is sealed on your hub before it leaves:
AES-256-GCM under a key derived from your passphrase (PBKDF2-HMAC-SHA256,
600 000 iterations), bound to its space, kind and id so the server cannot
swap two records or replay one workspace's record in another. Only this
envelope format (version 2) is written and opened; anything else is refused.
The linked hub
stores the envelope, a size, a date and which key sealed it. Titles, names
and content are inside the envelope. Your hub, and the browser, refuse a
record in an encrypted space that is not sealed with your key: the linked
hub cannot slip in a plain record of its own.

- **The passphrase.** Let the hub generate one (130 random bits, shown once)
  or type your own (at least 12 characters). It is never stored, here or
  there. Your hub keeps the derived key sealed under its own
  `AGENTS_HUB_SECRET_KEY`, so passes run unattended.
- **A second device** links to the same account and types the same
  passphrase; the linked hub keeps a verifier (a constant sealed with the
  key) so a wrong passphrase is refused before anything is opened.
- **Losing it.** The passphrase and every device that holds the key gone
  means the synced data cannot be opened by anyone. There is no recovery.
  A generated passphrase lost before anything was sealed with it can be
  replaced: generate again, and when the linked hub reports that no record
  is sealed with the key, Settings offers to replace it. The linked hub
  refuses that once a single record is sealed with the key.
- **The header shows it.** While sync is on, a pill in the header says
  whether it is encrypted, when the last pass ran, and whether something
  needs attention; a red pill means the key is locked on this hub.

### Viewing encrypted data on the linked hub

Signed in to the linked hub, a workspace holding your encrypted records says
so in a banner, and Synced data lists them as Encrypted, without titles. To
read them, choose Unlock to view, type your passphrase and agree that the
data is decrypted in that browser tab. The key and the plain text stay in
the tab: they are not sent to the server and not stored, and Lock (or
closing the tab) drops them. The linked hub never runs agents on encrypted
data and never turns it into pages; it cannot read it.

A space is either encrypted or not: switching a space that already holds
records the other way is refused, so a space is never half readable.

## Settings

| Where | What |
| --- | --- |
| Settings, Remote hub, Sync | Mappings, encryption, the key, Sync now, conflicts |
| `GET /api/hublink/sync` | Status, readable by anyone signed in (the header); others than administrators see only the workspaces they can see, and whether the last pass failed instead of its error text |
| `GET /api/hublink/sync/conflicts` | Conflicts, cut the same way for others than administrators |
| `PUT /api/hublink/sync` | `{enabled, encrypted, mappings: [{workspace, space}]}`, administrators |
| `POST /api/hublink/sync/key` | `{generate: true}`, `{generate: true, replace_unused: true}` or `{passphrase}`. When the account already has a key, `{generate: true}` answers 409 with `detail: {message, code: "key_exists", replace_unused}`, where `replace_unused` is true only when no record is sealed with it; other errors carry a sentence |
| `/api/sync/...` on the linked hub | The vault: spaces, keys (each with `records`, how many live records it sealed), push, changes, records; every route acts for the caller alone |
| `DELETE /api/sync/keys/{key_id}` on the linked hub | Your own key, refused while any record is sealed with it |

Writing into a space needs editor rights on that workspace on the linked
hub; reading it needs sight of it.

Related: [hub link](hub-link.md), [secrets](secrets.md), [identity](identity.md).
