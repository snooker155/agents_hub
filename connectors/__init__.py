"""
Connectors: the services this hub reaches out to with a token.

- ``trackers`` holds the Jira issue tracker (a credential connector,
  ``credentials.py``), ``git`` the GitLab provider, ``store.py`` the
  per-workspace configuration document both kinds use, and ``proposals.py``
  the connections an agent proposes from chat for a person to finish.
"""
