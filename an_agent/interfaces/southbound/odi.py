from __future__ import annotations

"""
Заглушка под ODI/other southbound интерфейсы.
"""


class ODISouthboundExecutor:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def apply(self, decision) -> None:
        raise NotImplementedError("ODI southbound is not implemented in skeleton.")
