from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field


class ProjectType(str, Enum):
    general = "general"
    code = "code"
    research = "research"
    documentation = "documentation"


class ProjectStatus(str, Enum):
    active = "active"
    archived = "archived"
    completed = "completed"


class RepoType(str, Enum):
    github = "github"
    gitlab = "gitlab"
    bitbucket = "bitbucket"
    local = "local"
    none = "none"


class RepoConfig(BaseModel):
    type: RepoType = RepoType.none
    url: Optional[str] = None          # Remote git URL
    branch: Optional[str] = "main"
    local_path: Optional[str] = None   # Path within workspace (relative)
    remote_id: Optional[str] = None    # Provider repo id ("owner/repo" / path_with_namespace)


class FrontendConfig(BaseModel):
    enabled: bool = False
    port: Optional[int] = None         # Port where frontend runs
    dev_command: Optional[str] = None  # e.g. "npm run dev"
    build_dir: Optional[str] = None    # e.g. "dist" or "build"
    url: Optional[str] = None          # Override URL for preview


class BackendConfig(BaseModel):
    enabled: bool = False
    port: Optional[int] = None         # Port where backend runs
    start_command: Optional[str] = None
    swagger_path: Optional[str] = "/docs"  # Path to swagger UI
    base_url: Optional[str] = None     # Override base URL


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: Optional[str] = None
    status: ProjectStatus = ProjectStatus.active
    type: ProjectType = ProjectType.general

    workspace: str                      # Must belong to a workspace

    repo: RepoConfig = Field(default_factory=RepoConfig)
    frontend: FrontendConfig = Field(default_factory=FrontendConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)

    tags: List[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        try:
            object.__setattr__(self, "updated_at", datetime.now(timezone.utc))
        except Exception:
            setattr(self, "updated_at", datetime.now(timezone.utc))
