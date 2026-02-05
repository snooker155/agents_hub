from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

from an_agent_core.types import ServiceMode


@dataclass
class ChoiceMakingConfig:
    default_mode: str = "embb"


class ChoiceMaker:
    """
    Choice-making:
    - применяет обновления от self-awareness: режим, reward, ограничения
    - на практике это будет влиять на:
      - reward_shaping (models/policy/reward_shaping.py)
      - rule thresholds (knowledge/rules)
      - параметры генерации/выбора goals
    """

    def __init__(
        self,
        cfg: ChoiceMakingConfig,
        reward_shaper: Optional[Any] = None,  # models.policy.reward_shaping.RewardShaper
        constraints_provider: Optional[Any] = None,  # knowledge.rules.constraints.ConstraintsProvider
    ) -> None:
        self.cfg = cfg
        self.reward_shaper = reward_shaper
        self.constraints_provider = constraints_provider

        self._mode: ServiceMode = ServiceMode(cfg.default_mode)

    def apply_updates(self, updates: Dict[str, Any]) -> None:
        # режим
        mode_update = updates.get("mode")
        if isinstance(mode_update, dict) and "set" in mode_update:
            new_mode = str(mode_update["set"]).lower()
            if new_mode in ("embb", "urllc"):
                self._mode = ServiceMode(new_mode)
                logger.info("Proactive: mode set to {} (reason={})", new_mode, mode_update.get("reason"))

                if self.reward_shaper is not None:
                    self.reward_shaper.set_mode(new_mode)

                if self.constraints_provider is not None:
                    self.constraints_provider.set_mode(new_mode)

        # производительность/деградация
        perf = updates.get("performance")
        if isinstance(perf, dict) and perf.get("degrade") is True:
            if self.constraints_provider is not None:
                self.constraints_provider.set_degraded(True, reason=str(perf.get("reason", "unknown")))
            logger.warning("Proactive: degrade enabled due to {}", perf)

        # любые другие обновления можно расширять
        # threshold_violation / error_seen и т.д.
