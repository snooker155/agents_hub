"""Keep heavy optional packages out of imports that do not need them.

langchain_core.language_models.base runs ``from transformers import
GPT2TokenizerFast`` at module level whenever transformers is installed, which
it is wherever the RAG extra is (sentence-transformers depends on it). That
one line pulls in transformers and torch, about four seconds on every start of
the API, a worker or a run subprocess, for a tokenizer only used by
``get_num_tokens`` on models that do not override it; nothing here calls it.

The guard below makes that single import fail as if transformers were absent.
Anyone else importing transformers, sentence-transformers above all, gets the
real package, and the guard removes itself once langchain_core has loaded.

langchain_core 1.x (what requirements.lock pins) imports transformers lazily,
inside ``get_tokenizer``, so there the guard never fires; it stays for an
environment still on 0.3 and costs one meta path entry.
"""
from __future__ import annotations

import sys

_BLOCKED = "transformers"
_IMPORTER = "langchain_core.language_models.base"


class _LangchainTransformersGuard:
    """A meta path finder that refuses ``transformers`` to one importer."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == _BLOCKED and _imported_from(_IMPORTER):
            _uninstall()
            raise ModuleNotFoundError(f"No module named {_BLOCKED!r}", name=_BLOCKED)
        return None


def _imported_from(module: str) -> bool:
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_globals.get("__name__") == module:
            return True
        frame = frame.f_back
    return False


_guard = _LangchainTransformersGuard()


def _uninstall() -> None:
    try:
        sys.meta_path.remove(_guard)
    except ValueError:
        pass


def install() -> None:
    """Install the guard, unless langchain_core or transformers is loaded already."""
    if _IMPORTER in sys.modules or _BLOCKED in sys.modules or _guard in sys.meta_path:
        return
    sys.meta_path.insert(0, _guard)
