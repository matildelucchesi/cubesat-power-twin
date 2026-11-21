"""Safe mode: an on-board rule that sheds load when the battery runs low.

The controller uses hysteresis: it enters safe mode when the state of charge
falls below `enter_soc` and leaves it only once the battery has recovered
above `exit_soc`. With a single threshold the spacecraft would switch on and
off at every small charge/discharge cycle around it ("chattering").
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class SafeModeController:
    enter_soc: float = 0.4
    exit_soc: float = 0.6
    in_safe: bool = False

    def __post_init__(self):
        if not 0 <= self.enter_soc < self.exit_soc <= 1:
            raise ValueError("need 0 <= enter_soc < exit_soc <= 1")

    def update(self, soc: float) -> bool:
        """Decide the mode for the next step from the measured state of charge."""
        if not self.in_safe and soc < self.enter_soc:
            self.in_safe = True
        elif self.in_safe and soc > self.exit_soc:
            self.in_safe = False
        return self.in_safe


def simulate_with_safe_mode(power_in_w, nominal_load_w: float, safe_load_w: float,
                            step_s: float, capacity_wh: float, initial_soc: float,
                            charge_eff: float = 0.9, discharge_eff: float = 0.95,
                            enter_soc: float = 0.4, exit_soc: float = 0.6):
    """Battery simulation with the controller in the loop.

    Unlike the open-loop model, the load depends on the state of charge, so
    the steps must be computed in order: measure SOC -> choose mode -> apply
    load -> update stored energy.

    Returns (soc, load_w, safe) arrays, one value per step.
    """
    p_in = np.asarray(power_in_w, dtype=float)
    ctrl = SafeModeController(enter_soc, exit_soc)
    dt_h = step_s / 3600.0
    energy = initial_soc * capacity_wh
    n = len(p_in)
    soc, load, safe = np.empty(n), np.empty(n), np.zeros(n, dtype=bool)
    for k in range(n):
        safe[k] = ctrl.update(energy / capacity_wh)
        load[k] = safe_load_w if safe[k] else nominal_load_w
        net = p_in[k] - load[k]
        energy += net * charge_eff * dt_h if net >= 0 else net / discharge_eff * dt_h
        energy = min(max(energy, 0.0), capacity_wh)
        soc[k] = energy / capacity_wh
    return soc, load, safe
