"""Power consumption of the spacecraft subsystems."""
import numpy as np


def average_load(loads: dict[str, float]) -> float:
    """Orbit-average power consumption [W].

    Every subsystem is always on, except the transmitter, which is weighted
    by its duty cycle.
    """
    always_on = sum(v for k, v in loads.items()
                    if k not in ("radio_tx", "radio_tx_duty_cycle"))
    return always_on + loads.get("radio_tx", 0.0) * loads.get("radio_tx_duty_cycle", 0.0)


def load_profile(n_steps: int, loads: dict[str, float]) -> np.ndarray:
    """Power consumption at each time step [W] (constant average for now)."""
    return np.full(n_steps, average_load(loads))
