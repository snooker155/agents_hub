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

A project that keeps skills in the Claude Code format brings them along:
`.claude/skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`,
optionally `allowed-tools` and `metadata.tags`) followed by the instructions,
plus any files next to it. **Sync from repositories** on the Skills page scans
the workspace folder, every project's cloned repository and every project
folder; cloning, connecting or pulling a project repository syncs its skills
on its own.

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
| `POST /api/skills/sync` | `{"workspace", "project_id"?}`: scan `.claude/skills` folders |
| `POST /api/skills/import-md` | `{"workspace", "content", "agent_id"?}`: create from SKILL.md text |
| `GET /api/skills/{id}/export` | the skill as SKILL.md |

Related: [memory](memory.md), [agents](agents.md), [marketplace](marketplace.md),
[projects](projects.md).
