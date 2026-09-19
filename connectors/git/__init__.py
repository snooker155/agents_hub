"""
Git hosting connectors (GitHub / GitLab).

Provides:
  - store        — file-backed token/config storage (.agents_hub/git_connectors.json)
  - providers    — REST API clients normalizing repos and issues across providers
  - git_ops      — token-safe git subprocess helpers (clone/pull)
  - issue_sync   — import/refresh repo issues as project tasks
"""
