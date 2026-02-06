from __future__ import annotations

"""
В skeleton online и offline одинаковы (mock).
Когда подключишь настоящий telemetry + southbound (E2/ODI/TR-069),
сюда переносится "боевой" сбор зависимостей.
"""

from .run_offline import main


if __name__ == "__main__":
    main()
