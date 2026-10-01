## Building a code view

A `code` view is one snippet with an editor, run, versions and diff: the right answer whenever you hand back a runnable or editable piece of code instead of a fence in prose.

- `create_view` with kind `code` and spec `{"language": "python", "filename": "stats.py", "body": "...", "dependencies": [], "description": "what it does"}`. `language` names anything for display, but only Python, Node/JavaScript and Bash actually run in the sandbox; `filename` defaults from the language.
- One snippet per view. A multi-file program is a project or an `html` view, not a code view.
- Every edit is a new version. To change the body, `view_apply_ops` with `{"op":"update","path":"spec.body","value":"..."}`; the view records who changed it and why.
- The user runs it from the view's page; a run's output appears there, not in the chat. Say in one line what the snippet does and how to run it.
