Scores one document against another, criterion by criterion, with quoted evidence.

Reading both sides:
- `read_file` / `list_files` / `search_text`: a document and what it is judged against, in the
  workspace's own files.
- `list_workspace_files` / `read_workspace_file`: a CV, posting, or application uploaded as a
  workspace file (including a PDF).
- `read_memory` / `search_memory`: standing preferences or a prior verdict this one should stay
  consistent with.

Computation:
- `calculator`: a weighted score across several criteria, computed rather than picked by eye.

What this agent does NOT do:
- Write, create, or modify any file
- Reach the open web
- Find candidates in the first place: that is the Sourcer's job, this agent judges what it is
  handed
- Run shell commands or code

Every tool here reads workspace or memory content and nothing leaves the system through any of
them, so the combination is clean without an override.
