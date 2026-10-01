# Workspace files

A workspace file is a file stored once in a workspace and referenced by its
id everywhere else: a chat turn, a memory pool, a task, an eval case, an
agent's tools. Upload a contract once, and the same `file_0123456789abcdef`
is what the chat attaches, what the knowledge pool indexes and what a task
run finds in its working directory. This is the Files API shape of the
Anthropic and OpenAI platforms, with the file belonging to a workspace
rather than to an account.

The Artifacts page (`/artifacts`; the old `/files` and `/artifacts/files`
redirect there with their query) lists the files of the selected workspace
next to the views its agents built (docs/views.md), which sit in a virtual
**Views** folder at the root. Three ways to look at the same list, remembered
per browser: cards (a card per folder, file and view, with a breadcrumb; the
open folder is `?folder=<path>`), a tree by path (folders closed until opened;
a search opens every folder with a match) and a flat table. Open a file with
`/files?file=<id>`: that is where a chat attachment, a citation or a "where
used" link lands; a view row, or the details button of its card, opens the
view's panel the same way (`?view=<id>`): the live card as a column, what
made it, Studio, its page, delete. A view's exports land here as files, and
an html view is built from them, so the two sit together.

The list is the drop target. Dragging files or folders over it turns it into
a drop field for the open folder (the root in the tree and the list): a
dropped folder keeps its structure, and everything lands in the workspace
folder at that path (`POST /api/files` with a `path` form field writes the
file there and registers it, replacing a file already at the path), where the
agents' file tools see it. A loose file dropped at the root goes to the file
store as before. The Views folder takes no files: views are built by agents,
so over it the field says so and the drop does nothing.

## The object

| Field | Meaning |
| --- | --- |
| `file_id` | `file_` plus 16 hex characters |
| `workspace` | the workspace the file belongs to; every use of the file is checked against it |
| `name` | the name as uploaded (any script, no path) |
| `mime_type` | the type the upload declared, or a guess from the name |
| `size`, `sha256` | bytes and their hash |
| `source` | where it came from: `upload`, `chat`, `agent`, `memory`, `task`, `eval`, `api` |
| `created_by` | the user (or, for `agent`, the agent) that added it |
| `created_at` | when |
| `meta` | free-form details: for `agent`, the run and agent that saved it; for `chat`, the conversation |

The content lives under the state root at `files/<workspace>/<file_id>/<safe
name>` and is mirrored to the object store when one is configured
(`AGENTS_HUB_BLOB_URL`, see [storage](storage.md)), so a worker or a replica
on another host reads it back. The catalogue is the `workspace_files` table
(migration 0021).

Uploading the same bytes into the same workspace returns the file that already
exists, whatever the new name: a document attached to ten chat turns is one
file. The same bytes in another workspace are a separate file.

Deleting a file removes its content, locally and in the object store, and
keeps the row as a tombstone (`deleted_at`), so an old chat turn, a citation
or an audit row can still name what it was. A deleted file cannot be read,
attached or added again; uploading the same bytes creates a new file.

## Files in the workspace folder

The workspace folder (`workspaces/<workspace>/` under the state root) is the
agents' working directory, and what they write there with `write_file`,
`create_file` and `apply_unified_diff` is a workspace file too, without a
copy: the tool registers the path (`files.service.register_path`) and the
record's content is the file in the folder. Its source is `agent`, `meta`
carries the workspace-relative `path` and the agent, run and session that
wrote it, and `created_at` is when the file was last written. Rewriting the
file updates the same record (size, hash, type), so an id a chat turn or a
task already holds keeps pointing at the file; `delete_file` tombstones it.
Deleting such a file on the Files page deletes it from the folder.

Hidden entries (a leading dot, where the hub keeps `.logs`, `.views`,
`.patch_backups`) and version control, cache, virtual environment and build
folders (`INDEX_SKIP_DIRS`) are never registered.

Files written before the registry followed the tools, or by a process outside
them (Claude Code, Codex, a shell in a sandbox), are picked up by an index of
the folder: **Sync from folder** on the Files page, `POST /api/files/index`
or `ah files index [workspace|--all]`. The index registers every file the
same way (a file under `knowledge/` gets source `memory`, under
`chat_uploads/` `chat`, under `task_files/` `task`, the rest `agent`), leaves
an unchanged file alone, updates a changed one in place and tombstones the
records of folder files that are gone. A file past the per-file limit or the
workspace quota is reported as skipped. The index needs the `editor` role in
the workspace and leaves an audit row (`file.index`).

## Limits

Two operator settings, read live from `.env` like the other settings:

| Setting | Default | Limits |
| --- | --- | --- |
| `AGENTS_HUB_FILES_MAX_FILE_MB` | 25 | one file |
| `AGENTS_HUB_FILES_MAX_WORKSPACE_MB` | 1024 | everything one workspace holds (deleted files do not count) |

An upload past either answers 413.

## Text for a model

Wherever a file's content goes into a prompt, the same extraction applies:
plain text, markdown, JSON, CSV, YAML, XML and source code are read as text;
a PDF is read with `pypdf` (the library the RAG ingest uses, `PyPDF2` as a
fallback) when it is installed; a file with an unknown type is read as text
when its first bytes are UTF-8 without a NUL. Anything else (an image, an
archive, a spreadsheet) is named with its type and size, never inlined.

`files.service.text_for_prompt(file_ids, budget_chars=40000)` produces
`File: name (id)` blocks within one character budget for all files together:
the file that crosses it is cut with a marker and the files after it are
named only.

## Where it is used

- **Chat.** The composer's attach menu has "From workspace files". An
  attachment sent as `{"file_id": "..."}` is loaded on the server
  (`chat/attachments.py`): the file must belong to the request's workspace
  (403 otherwise, 400 when the request names none). Its text goes into the
  prompt like an uploaded file's; a binary file is named and copied into the
  workspace's `chat_uploads/` so the path the prompt gives exists. An upload
  marked "store in workspace" is saved as a workspace file (source `chat`)
  the moment the box is ticked, shows "Saved to workspace files", and the
  turn sends it by id; the server still keeps its `chat_uploads/` copy,
  because the prompt points agents with filesystem tools there. See
  [chat](chat.md).
- **Memory.** The files tab of a memory pool has "Add from workspace files":
  `POST /api/shared-memory/{pool}/files/from-workspace` copies the file into
  the workspace's `knowledge/` folder and indexes it the way an upload is
  indexed. The pool's entry remembers `workspace_file_id`, which is how a
  citation of one of its passages links back to the file. A file that cannot
  be indexed (an image) answers 415 and leaves no copy behind. See
  [memory](memory.md).
- **Tasks.** A task carries `file_ids`. When a run starts, the files are
  copied into its working directory under `task_files/` (safe names, never
  overwriting, the same bytes reused on a re-run) and listed in the prompt.
  Subtasks, whether created with `add_subtask` or by `delegate_task_tool`,
  inherit the parent's files like its money cap. The task page has a Files
  card; the create form has a picker. See [tasks](tasks.md).
- **Evals.** A case carries `file_ids`. It runs in an isolated directory with
  the files copied in, and its input names them. See [evals](evals.md).
- **Agent tools.** `list_workspace_files`, `read_workspace_file` (text in
  slices of up to 50,000 characters, with `next_offset`) and
  `save_workspace_file` (text an agent produced becomes a file with source
  `agent`; the chat reply links it). The workspace is always the run's own.
  The readers grant `reads_private` like `read_file`; the writer grants
  nothing, like `write_file`. See [tools and capabilities](tools-and-capabilities.md).
  The filesystem tools (`write_file`, `create_file`, `apply_unified_diff`,
  `delete_file`) register what they write in the workspace folder as
  workspace files, see "Files in the workspace folder" above.

"Where used" (`GET /api/files/{id}/usage`) reads the pools, tasks and eval
cases back from their own records, and the chat conversations from the
`workspace_file_uses` table, the one place a chat turn leaves a trace of the
file after the request is over.

## Citations

`search_memory` and `recall` number every document passage and every note
they return on the run's citation sink (`common/citation_sink.py`) and put the
number on the result as `cite`; the tool output tells the model to cite what
it uses as `[n]`. The same passage found twice in one turn keeps its number.
Core memory blocks, slots and episodes are not numbered: blocks are already in
the agent's prompt, and a slot or an episode is structured data or a log
entry, not a source a reader can open.

A pool's indexed passages are searched whether or not a vector store is
configured: without one, BM25 over the chunk store answers alone.

A citation is `{n, pool_id, file_id, filename, chunk_idx, heading_path,
snippet, score, workspace_file_id, layer}`. The chat's `done` event carries
the turn's list as `citations`; the reply renders the sources under the text,
each `[n]` in the text becomes a link that scrolls to and highlights its
source, and a source links to its workspace file (`/files?file=<id>`) or,
without one, to the memory page. The run record keeps the list as
`citations`, and the run page shows it. A task run (`runtime/agent_run.py`)
and each node of a flow chat install their own sink, so their run records
carry their citations too.

## API

| Method and path | What it does |
| --- | --- |
| `GET /api/files?workspace=&q=&source=&limit=` | the workspace's files, newest first, with `usage_bytes` and `limits` |
| `POST /api/files?workspace=` | upload, multipart `file` (and optional `source`); `deduplicated: true` when the bytes were already stored |
| `POST /api/files/index?workspace=` | register the files of the workspace folder and drop the records of gone ones; `{added, updated, unchanged, removed, skipped}` |
| `GET /api/files/{id}` | the record |
| `GET /api/files/{id}/text?max_chars=` | the text as agents see it (`kind`: text, pdf or binary) |
| `GET /api/files/{id}/content` | the bytes, as a download |
| `GET /api/files/{id}/usage` | chats, memory pools, tasks and eval cases that reference it |
| `DELETE /api/files/{id}` | delete (content removed, tombstone kept) |
| `POST /api/shared-memory/{pool}/files/from-workspace` | add a file to a memory pool, `{"file_id"}` |

Reading needs the file's workspace to be visible to the caller; upload and
delete need the `editor` role there. Upload and delete leave audit rows
(`file.upload`, `file.delete`). A download goes out with
`Content-Disposition: attachment`, `X-Content-Type-Options: nosniff` and a
sandboxing Content-Security-Policy, and a type a browser would run as a page
(HTML, SVG, XML, script) is served as plain text.

From Python, `files.service` is the same surface: `create_file`, `get_file`,
`list_files`, `read_bytes`, `local_path`, `delete_file`, `materialize` and
`text_for_prompt`.
