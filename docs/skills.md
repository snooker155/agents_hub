# Skills

A skill is a reusable procedure an agent can follow: written once, surfaced when
it is relevant to the task at hand.

## The part that trips everyone up

A skill sitting in your workspace does nothing on its own. **Attaching it to an
agent** is what puts it in that agent's prompt, and attaching turns on that
agent's skills setting. Without that, the skill tools are never wired up and the
skill is silently inert.

## How they surface

Skills are the procedural memory layer: the catalog is injected into the system
prompt, and the agent fetches a skill's content with `get_skill` when it decides
one applies. `list_skills` is deliberately not a tool: the catalog is already
in the prompt.

## Writing one

Keep it to a single procedure with a clear trigger. The catalog line is what the
agent matches against, so it should say when to use the skill, not summarise its
contents.

A skill holds steps, free Markdown instructions, or both. Instructions are
what a SKILL.md carries under its frontmatter; `get_skill` returns them as
`instructions` next to the steps.

## Versions

Every change to a skill's content (name, description, steps, instructions,
tags, files) is a version: an edit on the Skills page, an agent's
`create_skill`, an install, a restore, a sync from a repository. A use of the
skill is not. **History** on a skill card lists the versions with who made
each one and a line diff against the version before it. **Restore** puts the
skill back to a past version; the restore is a version of its own, so it can
be undone the same way.

A skill attached to an agent can be **pinned** to one version from the same
panel. The agent then reads that version in its prompt catalog and from
`get_skill`, whatever happens to the skill afterwards, so it keeps the text
it was tested with. Unpin and it follows the latest version again.

## Installed copies and their original

Installing copies a skill; the copy remembers which version of the original it
holds. When the original moves on, the copy shows **update to vN**: taking it
replaces the copy's content with the original's current one (the copy's own
text stays in its history).

## Skills from a repository

A project that keeps skills in the Agent Skills format brings them along: a
folder per skill with a `SKILL.md` holding YAML frontmatter (`name`,
`description`, optionally `license`, `compatibility`, `allowed-tools` and
`metadata.tags`) followed by the instructions, plus any files next to it.
**Sync from repositories** on the Skills page scans the workspace folder,
every project's cloned repository and every project folder; cloning,
connecting or pulling a project repository syncs its skills on its own.

The scan is a bounded walk, not a fixed path, so the layouts public
collections use all work: `.claude/skills/<name>`, `skills/<name>`,
`plugins/<name>`, `.github/plugins/<bundle>/skills/<name>` and a single skill
at the root of its own repository. It goes six levels deep, skips `.git`,
`node_modules` and the like, never follows a symlinked folder and does not
look for skills inside a skill folder.

- A new folder becomes a catalog entry marked "from a repository" at version 1.
- A changed SKILL.md or file list becomes a new version. Copies attached to
  agents in the same workspace follow it when nobody edited the copy since it
  was taken; an edited copy keeps its text and shows that an update is
  available; a pinned copy keeps serving its pinned version either way.
- A folder that disappeared marks its entry "removed from the repository"
  instead of deleting it, so an agent does not lose a skill when a branch
  changes. Delete it from the card when it is really gone.
- A SKILL.md that cannot be read (no frontmatter, no description, over
  256 KB) is listed in the sync result and skipped.

The catalog entry of a repository skill is changed in the repository, not on
the Skills page: edit its SKILL.md there and sync. An attached copy is the
agent's own and can be edited.

The files next to SKILL.md reach the agent through `read_skill_file(name,
path)`, which the agent gets only when one of its skills has files. It reads
only files listed for that skill, inside its folder: symlinks and paths that
leave the folder are refused, and a read stops at 200 KB.

## Safety review

A skill is text an agent trusts, so a skill from outside the team is a supply
chain input like a package. Snyk's ToxicSkills audit (February 2026, 3984
skills from ClawHub and skills.sh) found a third with security issues and 76
confirmed malicious ones, almost all of them combining prompt injection in
SKILL.md with code in the folder. Every skill that comes from a repository or
from pasted SKILL.md text is therefore reviewed on import and on every sync
(`memory/skill_review.py`), and the result sits on the card:

- **Flags**: patterns in the description, the instructions and the text files
  next to them. The same patterns the [web log](web-logs.md) uses for a
  fetched page (text telling the reader to discard its instructions, fake
  speaker turns, requests to send data somewhere, installers piped to a shell,
  credential-shaped strings) plus the shapes malicious skills take: turning
  off the agent's permission checks, decoding and running base64, reaching
  for credential files or the environment, writing the agent's own
  configuration, downloading archives or installers, and installing
  dependencies without a pinned version (including PEP 723 inline metadata).
  A medium flag in the description counts as high: that line is in the
  agent's prompt whenever the skill is attached, before the agent decided to
  use it. Expand the card to read each flag with its excerpt.
- **Scripts**: the files an agent could run, by extension or under `scripts/`
  and `bin/`. The hub only hands their text to the agent through
  `read_skill_file`; running them is the agent's `run_code` or shell, so give
  such a skill only to agents in an [environment](environments.md) without
  secrets or network access. An opaque binary (`.exe`, `.pyc`, `.so`, `.jar`)
  is a high flag of its own, since nobody can read it.
- **License**: the `license` field of SKILL.md, or the LICENSE file next to
  it when the field is empty. A license that is not open (proprietary,
  source-available, all rights reserved) marks the skill **not open source**:
  it can be used in the workspace that imported it but **Publish** and the
  registry's submit refuse it, since publishing would redistribute it.
  Skills that name no license are publishable; most hand-written ones name
  none.

Flags are signals, not verdicts, and nothing is blocked: a skill about prompt
injection gets flagged for quoting one. The sync result and the source
list count the flagged skills, and the doctor's `skills` check
([service health](service-health.md#check-skills)) warns while a skill
attached to an agent carries a high flag. A skill edited or restored here is
reviewed again; a skill nobody reviewed (hand-written, or synced by an older
build) shows no review, and the next sync gives a repository skill one
without counting it as a change. For a deeper, offline second opinion
install Cisco's open-source `skill-scanner` (Apache 2.0); the doctor says
whether it is on the path.

## Sources

**Sources** on the Skills page lists public repositories of skills whose
license was checked by hand (`memory/skill_sources.py`): Anthropic's
reference collection, Sentry, Hugging Face, Microsoft, Trail of Bits, Expo,
Cloudflare and one community collection, each with its publisher and
license. **Connect** clones the repository as a project of the workspace
(tagged `skills`, under `<project>/repo`) and syncs its skills; from then on
it is an ordinary project, **Pull** brings new versions in and the catalog
entries follow as described above. Any other `https` repository on
github.com, gitlab.com or bitbucket.org can be connected by URL (or
`owner/name` for GitHub) and goes through the same review; a repository the
workspace already has is synced again, not cloned twice.

Not on the list, on purpose: open marketplaces and install leaderboards
(skills.sh, ClawHub), where the audit above found the malicious skills, and
link indexes such as VoltAgent/awesome-agent-skills, which hold no skills
themselves. A repository's license applies to its skills unless a SKILL.md
says otherwise: anthropics/skills is Apache 2.0, but its `docx`, `pdf`,
`pptx` and `xlsx` skills declare a proprietary license and come in marked
not open.

Pin what you attach. A connected source moves with its upstream; an attached
copy that is pinned to the version you reviewed keeps that text whatever the
next pull brings, and the card says when an update is available.

## SKILL.md in and out

**Import SKILL.md** takes pasted text or a file and creates a skill from it.
**SKILL.md** on a card downloads the skill in that format (steps become a
numbered list after the instructions), ready to commit into a
`.claude/skills` folder.

## API

| Endpoint | What it does |
| --- | --- |
| `GET /api/skills/{id}/versions` | versions, newest first, plus `current` and `pinned` |
| `GET /api/skills/{id}/versions/{n}` | one version with its `snapshot` |
| `POST /api/skills/{id}/versions/{n}/restore` | put the skill back to version n |
| `PUT /api/skills/{id}/pin` | `{"version": n}` pins an attached skill, `null` unpins |
| `POST /api/skills/{id}/update-from-origin` | take the original's current content into a copy |
| `POST /api/skills/sync` | `{"workspace", "project_id"?}`: scan the skill folders; the report lists `flagged` |
| `POST /api/skills/import-md` | `{"workspace", "content", "agent_id"?}`: create from SKILL.md text, reviewed |
| `GET /api/skills/{id}/export` | the skill as SKILL.md |
| `GET /api/skills/sources?workspace=` | the curated sources with license, connection state and counts, plus repositories added by URL |
| `POST /api/skills/sources` | `{"workspace", "url", "branch"?, "name"?}`: clone as a project and sync; returns `project`, `sync`, `already_present` |

Every skill in the API carries `license`, `publishable` and `safety`
(`severity`, `flags[]`, `scripts[]`, `license_open`, `scanned_at`, or null
when nobody reviewed it). Publishing a skill whose `publishable` is false
answers 409.

Related: [memory](memory.md), [agents](agents.md), [marketplace](marketplace.md),
[projects](projects.md), [environments](environments.md),
[service health](service-health.md).
