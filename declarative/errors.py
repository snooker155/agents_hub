"""Errors of the declarative engine, each able to say where in which file."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Problem:
    """One thing wrong with the declared files: ``file:line: message``."""
    source: str
    line: Optional[int]
    message: str

    def __str__(self) -> str:
        where = f"{self.source}:{self.line}" if self.line else self.source
        return f"{where}: {self.message}"


class DeclarativeError(Exception):
    """Base class: anything the engine refuses."""


class ValidationError(DeclarativeError):
    """The files do not describe a valid bundle. Raised before any write."""

    def __init__(self, problems: List[Problem]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(str(p) for p in self.problems))


class PlanBlocked(DeclarativeError):
    """``apply`` was handed a plan with drift, a field that cannot change in
    place, or a reference that resolves to nothing. Nothing was written."""


__all__ = ["Problem", "DeclarativeError", "ValidationError", "PlanBlocked"]
