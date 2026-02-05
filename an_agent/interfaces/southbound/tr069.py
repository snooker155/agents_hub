from __future__ import annotations

"""
Заглушка под TR-069 / CWMP управление.
"""


class TR069SouthboundExecutor:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def apply(self, decision) -> None:
        raise NotImplementedError("TR-069 southbound is not implemented in skeleton.")
