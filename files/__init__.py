"""
Workspace files: a file uploaded once and referenced by its id in chat,
memory, tasks and evals. See ``files/service.py`` and docs/files.md.
"""
from files.service import (  # noqa: F401 - the package's public surface
    FileError,
    FileTooLarge,
    WorkspaceQuotaExceeded,
    create_file,
    delete_file,
    get_file,
    get_files,
    list_files,
    local_path,
    materialize,
    read_bytes,
    text_for_prompt,
)

__all__ = [
    "FileError", "FileTooLarge", "WorkspaceQuotaExceeded", "create_file", "delete_file",
    "get_file", "get_files", "list_files", "local_path", "materialize", "read_bytes",
    "text_for_prompt",
]
