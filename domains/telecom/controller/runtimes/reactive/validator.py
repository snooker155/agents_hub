from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from domains.telecom.controller.an_agent_core.clock import Deadline
from domains.telecom.controller.an_agent_core.types import Action, WorldState


@dataclass
class ValidatorConfig:
    enforce_mcs_step: bool = True


class Validator:
    """
    Проверяет действие на:
    - domain constraints (диапазоны, max_step)
    - safety (не ухудшать при URLLC, например)
    - (опц.) predictive checks (если есть прогноз)
    """

    def __init__(self, cfg: ValidatorConfig) -> None:
        self.cfg = cfg

    def validate(self, action: Action, ws: WorldState, deadline: Deadline) -> Tuple[bool, Dict[str, Any]]:
        notes: Dict[str, Any] = {}
        c = ws.constraints or {}

        if action.type != "SET_RADIO_PARAMS":
            # разрешаем NO_OP и любые другие в будущем
            return True, {"info": "non_radio_action"}

        mcs = action.params.get("mcs_index")
        rank = action.params.get("mimo_rank")

        # диапазоны
        mcs_min, mcs_max = int(c.get("mcs_min", 0)), int(c.get("mcs_max", 27))
        if mcs is None or not (mcs_min <= int(mcs) <= mcs_max):
            return False, {"reason": "mcs_out_of_range", "mcs": mcs, "range": [mcs_min, mcs_max]}

        allowed_ranks = c.get("mimo_allowed", [1, 2, 4])
        if rank is None or int(rank) not in [int(r) for r in allowed_ranks]:
            return False, {"reason": "rank_not_allowed", "rank": rank, "allowed": allowed_ranks}

        # max_step
        if self.cfg.enforce_mcs_step:
            current_mcs = c.get("current_mcs")
            max_step = int(c.get("mcs_max_step", 2))
            if current_mcs is not None:
                if abs(int(mcs) - int(current_mcs)) > max_step:
                    return False, {
                        "reason": "mcs_step_too_large",
                        "current_mcs": int(current_mcs),
                        "requested_mcs": int(mcs),
                        "max_step": max_step,
                    }

        # предиктивный safety чек: если URLLC и прогноз BLER высокий — не повышать MCS
        if ws.service_mode.value == "urllc" and ws.bler_forecast:
            bler0 = ws.bler_forecast[0]
            urllc_max = float(c.get("urllc_bler_max", 0.001))
            current_mcs = c.get("current_mcs")
            if bler0 > urllc_max and current_mcs is not None and int(mcs) > int(current_mcs):
                return False, {
                    "reason": "urllc_no_increase_when_bad_bler",
                    "bler0": bler0,
                    "urllc_max": urllc_max,
                    "current_mcs": int(current_mcs),
                    "requested_mcs": int(mcs),
                }

        notes["ok"] = True
        return True, notes

    @staticmethod
    def expected_outcome(ws: WorldState) -> Dict[str, Any]:
        return {
            "bler_forecast": ws.bler_forecast,
            "sinr_est_db": ws.sinr_est_db,
            "tpt_smoothed_mbps": ws.tpt_smoothed_mbps,
        }
