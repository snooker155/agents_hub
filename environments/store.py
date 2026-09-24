"""Where environments live: the ``environments`` DocStore collection.

One document per environment, keyed by its id, in the shared ``documents``
table (common/docstore.py), so every replica and every worker resolves the
same profile for a run. There is no legacy file to import: environments are
new with this store. The service layer (environments/service.py) owns every
rule; this module only reads and writes whole records.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, List, Optional

from common.docstore import DocStore

from .models import Environment

log = logging.getLogger(__name__)

_store = DocStore("environments")


@contextmanager
def transaction() -> Iterator[None]:
    """Atomic read-modify-write across the collection (uniqueness and the one
    default per scope are checked and written under it)."""
    with _store.transaction():
        yield


def get(env_id: str) -> Optional[Environment]:
    raw = _store.get(str(env_id or ""))
    if not isinstance(raw, dict):
        return None
    try:
        return Environment.model_validate(raw)
    except Exception:  # noqa: BLE001 - a record that no longer validates is reported as absent, not as a crash
        log.warning("environment %s does not validate, ignored", env_id, exc_info=True)
        return None


def all() -> List[Environment]:  # noqa: A001 - mirrors DocStore.all
    out: List[Environment] = []
    for key, raw in _store.all().items():
        if not isinstance(raw, dict):
            continue
        try:
            out.append(Environment.model_validate(raw))
        except Exception:  # noqa: BLE001 - same as get(): skip a record that no longer validates
            log.warning("environment %s does not validate, ignored", key, exc_info=True)
    return out


def put(env: Environment) -> None:
    _store.put(env.id, env.model_dump(mode="json"))


def delete(env_id: str) -> bool:
    return _store.delete(str(env_id))


def clear() -> int:
    return _store.clear()
