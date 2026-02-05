from __future__ import annotations

"""
Заглушка под E2 control (O-RAN).
Здесь будет драйвер, который:
- мапит Action -> E2SM сообщений
- отправляет в near-RT RIC / E2 node
"""


class E2SouthboundExecutor:
    def __init__(self, *args, **kwargs) -> None:  # noqa: D401
        pass

    def apply(self, decision) -> None:
        raise NotImplementedError("E2 southbound is not implemented in skeleton.")
