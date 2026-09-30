# Every process imports common before langchain, so this is the one place that
# keeps langchain_core from loading transformers and torch at import time.
from common import import_guards as _import_guards

_import_guards.install()
