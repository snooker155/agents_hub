"""Request bodies shared by the routes of the model runtime."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DownloadBody(BaseModel):
    repo: str
    #: A GGUF file in the repo, or
    file: str = ""
    #: the name of a speech model GET /hf/files listed under ``packages``.
    package: str = ""
    revision: str = "main"


class LoadBody(BaseModel):
    file: str
    context_length: int = Field(default=4096, ge=256, le=1_048_576)
    gpu_layers: int = -1
    threads: Optional[int] = Field(default=None, ge=1, le=512)


class UnloadBody(BaseModel):
    file: str
