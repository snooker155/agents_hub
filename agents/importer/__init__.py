"""Importing a finished agent from its own git repository.

An agent that already exists and is already tested — living in its own repo,
with its own dependencies and its own model access — should not have to be
rewritten as a hub definition to be usable here. This package clones such a
repository, reads the small manifest it publishes (:mod:`.manifest`), reports
whether it can actually run in this hub (:mod:`.checks`), and registers it as a
``type="remote"`` agent (:mod:`.service`) that
:class:`agents.remote_agent.RemoteAgent` drives over HTTP.

The import never silently fails: an agent that does not yet meet the contract is
registered too, carrying the list of what is missing.
"""
from agents.importer.service import ImportError_, cleanup, inspect, recheck, register

__all__ = ["inspect", "register", "recheck", "cleanup", "ImportError_"]
