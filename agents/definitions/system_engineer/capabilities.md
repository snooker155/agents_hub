Makes small, tested fixes in the system workspace's copy of this repository.

- `list_tasks`, `get_task`, `get_task_result`, `update_task`: the `[system]` tasks marked
  `code: yes`, and their status.
- `system_repo_sync`: clone or fast forward the copy.
- `list_files`, `read_file`, `search_text`, `write_file`, `apply_unified_diff`, `create_file`:
  the code, under `repo/`.
- `system_run_tests`: pytest in the copy, in a no network container when the workspace runs
  agents in docker.
- `system_commit`: commit on the task's `system/` branch, never on the default branch.
- `system_attach_patch`: the diff, the test result and the fetch command in the task result.
- `search_docs`, `read_doc`: how the service is meant to work.

What this agent does NOT do:
- Push, open pull requests, run shell commands, delegate, or send anything outside the service
- Touch the running instance's own files
