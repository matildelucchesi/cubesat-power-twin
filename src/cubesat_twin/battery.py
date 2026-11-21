"""Battery state of charge."""
import numpy as np


def simulate_battery(power_in_w, power_out_w, step_s: float, capacity_wh: float,
                     initial_soc: float, charge_eff: float = 0.9,
                     discharge_eff: float = 0.95) -> np.ndarray:
    """State of charge (0-1) at each time step, from an energy balance.

    At each step the net power charges or discharges the battery:
      - surplus  -> stored energy grows by surplus * charge_eff * dt
      - deficit  -> stored energy drops by deficit / discharge_eff * dt
    Energy is clipped between 0 and the capacity: when the battery is full
    the extra power is dissipated by the power system.
    """
    p_in = np.asarray(power_in_w, dtype=float)
    p_out = np.asarray(power_out_w, dtype=float)
    dt_h = step_s / 3600.0
    energy = initial_soc * capacity_wh
    soc = np.empty(len(p_in))
    for k, net in enumerate(p_in - p_out):
        if net >= 0:
            energy += net * charge_eff * dt_h
        else:
            energy += net / discharge_eff * dt_h
        energy = min(max(energy, 0.0), capacity_wh)
        soc[k] = energy / capacity_wh
    return soc
