"""
langchain_core must not pull transformers and torch into every process.

It imports transformers at module level whenever the package is installed,
which costs seconds on each start of the API, a worker and a run subprocess.
common.import_guards turns that one import away and nothing else. Each check
runs in a fresh interpreter, since this test process may have loaded either
package already.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("transformers") is None
    or importlib.util.find_spec("langchain_core") is None,
    reason="needs transformers and langchain_core installed",
)


def _run(code: str) -> str:
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_langchain_core_loads_without_transformers():
    out = _run(
        "import sys, common\n"
        "import langchain_core.language_models.base as base\n"
        "print('transformers' in sys.modules, 'torch' in sys.modules, base._HAS_TRANSFORMERS)"
    )
    assert out == "False False False"


def test_the_guard_leaves_once_langchain_core_has_loaded():
    out = _run(
        "import sys, importlib.util, common\n"
        "from common import import_guards\n"
        "import langchain_core.language_models.base\n"
        "print(import_guards._guard in sys.meta_path,"
        " importlib.util.find_spec('transformers') is not None)"
    )
    assert out == "False True"


def test_other_importers_still_get_transformers():
    out = _run(
        "import sys, common\n"
        "from common import import_guards\n"
        "import transformers.utils.versions\n"
        "print('transformers' in sys.modules, import_guards._guard in sys.meta_path)"
    )
    assert out == "True True"
