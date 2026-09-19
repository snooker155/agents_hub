"""Agents Hub dashboard (backend API + frontend assets).

This file is intentionally present: it makes ``dashboard`` a *regular* package
rather than an implicit namespace package. Without it, any other project on
``sys.path`` that also ships a ``dashboard/`` directory (e.g. an editable
install of an unrelated repo) can contribute a namespace portion that shadows
``dashboard.backend`` and breaks ``uvicorn dashboard.backend.main:app``.
"""
