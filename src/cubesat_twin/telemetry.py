"""Representative housekeeping telemetry of the power system, with injected faults.

The twin needs telemetry to stay synchronised with. Real CubeSat telemetry
rarely comes with labelled faults, so this module plays the role of the
spacecraft: a "truth" model that is deliberately richer than the twin, with
parameters the twin does not know, and faults injected at a known time.
Because the ground truth is known, every detection, diagnosis and prognosis
method can be benchmarked on it.

What the truth model has and the twin does not:

* real tumbling: the spacecraft spins about a random axis, so the array power
  swings with the angle of each face to the Sun (the twin only uses the
  orbit average, total cell area / 4);
* battery capacity and internal resistance that differ from the datasheet;
* a transmitter switched on and off in blocks, and a hysteresis safe mode;
* array curtailment: with a full battery the power system takes from the
  array only what the loads draw;
* sensor noise on every channel, and a battery voltage that follows the
  open-circuit voltage curve of a Li-ion cell.

Telemetry channels (one sample per simulation step), as an EPS would report:
p_array_w, p_load_w, v_batt_v, i_batt_a (positive = charging), tx_on, safe.
The state of charge and the battery capacity are not measured: the twin
has to estimate them.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from eclipse import sun_direction
from loads import SHED_IN_SAFE_MODE
from power import solar_irradiance

FAULT_KINDS = ("nominal", "string_loss", "array_degradation", "capacity_fade", "load_anomaly")
FACES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")

# Open-circuit voltage of one Li-ion 18650 cell against state of charge
# (typical NMC curve). The 1U pack has two cells in series.
OCV_SOC = np.linspace(0, 1, 11)
OCV_CELL_V = np.array([3.00, 3.45, 3.60, 3.68, 3.73, 3.78, 3.85, 3.93, 4.01, 4.09, 4.20])
CELLS_IN_SERIES = 2
R_INTERNAL_OHM = 0.12          # pack internal resistance (datasheet value)

# Sensor noise (1 sigma) of the telemetry channels.
NOISE = {"p_array_w": 0.02, "p_array_rel": 0.01, "p_load_w": 0.02,
         "v_batt_v": 0.01, "i_batt_a": 0.005}

TX_BLOCK_MIN = 10              # the transmitter is switched in blocks of this length
TUMBLE_RATE_DEG_S = (1.0, 4.0)  # range of tumbling rates


def ocv(soc) -> np.ndarray:
    """Open-circuit voltage of the pack [V] at a state of charge."""
    return CELLS_IN_SERIES * np.interp(np.clip(soc, 0, 1), OCV_SOC, OCV_CELL_V)


def docv_dsoc(soc) -> np.ndarray:
    """Slope of the open-circuit voltage curve [V per unit SOC]."""
    slopes = np.diff(OCV_CELL_V) / np.diff(OCV_SOC)
    idx = np.clip(np.searchsorted(OCV_SOC, np.clip(soc, 0, 1), side="right") - 1, 0, len(slopes) - 1)
    return CELLS_IN_SERIES * slopes[idx]


@dataclass
class Scenario:
    """One telemetry run. `magnitude` depends on the fault kind:

    string_loss        fraction of one face's cells lost at onset (the face is chosen by the seed)
    array_degradation  fraction of array output lost per day after onset
    capacity_fade      fraction of battery capacity lost per day after onset
    load_anomaly       extra load [W] from onset (e.g. a heater stuck on)
    """
    kind: str = "nominal"
    onset_h: float = 72.0
    magnitude: float = 0.0
    seed: int = 0

    def __post_init__(self):
        if self.kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind {self.kind!r}, expected one of {FAULT_KINDS}")

    @property
    def failed_face(self) -> str | None:
        return FACES[self.seed % 6] if self.kind == "string_loss" else None


def face_illumination(times: pd.Series, rng: np.random.Generator) -> np.ndarray:
    """cos(angle to the Sun) of each face for a tumbling spacecraft, clipped at
    zero (a face facing away gets no light). Shape (n_steps, 6), faces in
    FACES order. The spacecraft spins about a random axis fixed in space."""
    s = sun_direction(times)
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    rate = np.radians(rng.uniform(*TUMBLE_RATE_DEG_S))
    t = (times - times.iloc[0]).dt.total_seconds().to_numpy()
    theta = -(rate * t + rng.uniform(0, 2 * np.pi))[:, None]
    # Sun direction in the body frame: rotate s by -theta about the axis (Rodrigues).
    body = (s * np.cos(theta) + np.cross(axis, s) * np.sin(theta)
            + axis * (s @ axis)[:, None] * (1 - np.cos(theta)))
    return np.clip(np.column_stack([body[:, 0], -body[:, 0], body[:, 1], -body[:, 1],
                                    body[:, 2], -body[:, 2]]), 0, None)


def generate(df: pd.DataFrame, config: dict, scenarios: list[Scenario],
             tx_duty: float | None = None) -> dict[str, np.ndarray]:
    """Telemetry and ground truth for a batch of scenarios on one orbit.

    df: twin output with columns time and sunlit (from simulation.run).
    Returns a dict of arrays of shape (n_scenarios, n_steps): the telemetry
    channels, plus truth_* arrays that the twin must not use.
    """
    times, sunlit = df.time, df.sunlit.to_numpy(dtype=float)
    step_s = config["simulation"]["step_s"]
    dt_h = step_s / 3600
    n, S = len(times), len(scenarios)
    hours = np.arange(n) * dt_h
    irr = solar_irradiance(times) * sunlit

    array = config["solar_array"]
    areas = np.array([array["faces"][f] for f in FACES])
    eff = array["cell_efficiency"]
    bat, loads = config["battery"], config["loads"]
    duty = loads.get("radio_tx_duty_cycle", 0.5) if tx_duty is None else tx_duty
    base_w = sum(v for k, v in loads.items() if k not in SHED_IN_SAFE_MODE)
    payload_w, tx_w = loads.get("payload", 0.0), loads.get("radio_tx", 0.0)
    safe_cfg = config.get("safe_mode", {})
    safe_on = safe_cfg.get("enabled", True)
    enter, exit_ = safe_cfg.get("enter_soc", 0.4), safe_cfg.get("exit_soc", 0.6)

    p_avail = np.empty((S, n))
    capacity = np.empty((S, n))
    extra_load = np.zeros((S, n))
    array_factor = np.ones((S, n))
    tx_on = np.empty((S, n), dtype=bool)
    r_int = np.empty(S)
    rngs = [np.random.default_rng(sc.seed) for sc in scenarios]
    block = max(1, int(TX_BLOCK_MIN * 60 / step_s))
    for i, (sc, rng) in enumerate(zip(scenarios, rngs)):
        after = hours >= sc.onset_h
        days_since = np.where(after, (hours - sc.onset_h) / 24, 0.0)
        face_area = np.tile(areas, (n, 1))
        if sc.kind == "string_loss":
            face_area[after, FACES.index(sc.failed_face)] *= 1 - sc.magnitude
        if sc.kind == "array_degradation":
            array_factor[i] = np.clip(1 - sc.magnitude * days_since, 0, 1)
        cap0 = bat["capacity_wh"] * rng.uniform(0.92, 1.0)  # real cells are below datasheet
        fade = np.clip(1 - sc.magnitude * days_since, 0.05, 1) if sc.kind == "capacity_fade" else 1.0
        capacity[i] = cap0 * fade
        if sc.kind == "load_anomaly":
            extra_load[i] = np.where(after, sc.magnitude, 0.0)
        illum = face_illumination(times, rng)
        p_avail[i] = irr * eff * (illum * face_area).sum(axis=1) * array_factor[i]
        blocks = rng.random(n // block + 1) < duty
        tx_on[i] = np.repeat(blocks, block)[:n]
        r_int[i] = R_INTERNAL_OHM * rng.uniform(0.9, 1.3)

    ch_eff, dis_eff = bat.get("charge_efficiency", 0.9), bat.get("discharge_efficiency", 0.95)
    energy = bat["initial_soc"] * capacity[:, 0]
    in_safe = np.zeros(S, dtype=bool)
    soc = np.empty((S, n))
    safe = np.zeros((S, n), dtype=bool)
    p_array, p_load, p_batt = np.empty((S, n)), np.empty((S, n)), np.empty((S, n))
    for k in range(n):
        s_now = energy / capacity[:, k]
        if safe_on:
            in_safe = np.where(~in_safe & (s_now < enter), True,
                               np.where(in_safe & (s_now > exit_), False, in_safe))
        safe[:, k] = in_safe
        load = base_w + extra_load[:, k] + np.where(in_safe, 0.0, payload_w + tx_w * tx_on[:, k])
        net = p_avail[:, k] - load
        headroom = np.maximum(capacity[:, k] - energy, 0) / (ch_eff * dt_h)
        pb = np.minimum(net, headroom)       # with a full battery the array is curtailed
        energy = energy + np.where(pb >= 0, pb * ch_eff, pb / dis_eff) * dt_h
        energy = np.clip(energy, 0, capacity[:, k])
        p_array[:, k] = np.minimum(p_avail[:, k], load + np.maximum(pb, 0))
        p_load[:, k], p_batt[:, k] = load, pb
        soc[:, k] = energy / capacity[:, k]

    v_oc = ocv(soc)
    current = p_batt / v_oc
    voltage = v_oc + r_int[:, None] * current
    noise = np.stack([rng.standard_normal((5, n)) for rng in rngs], axis=1)
    return {
        "hours": hours,
        "p_array_w": np.maximum(p_array * (1 + NOISE["p_array_rel"] * noise[0])
                                + NOISE["p_array_w"] * noise[1], 0),
        "p_load_w": p_load + NOISE["p_load_w"] * noise[2],
        "v_batt_v": voltage + NOISE["v_batt_v"] * noise[3],
        "i_batt_a": current + NOISE["i_batt_a"] * noise[4],
        "tx_on": tx_on & ~safe,
        "safe": safe,
        "truth_soc": soc,
        "truth_capacity_wh": capacity,
        "truth_array_factor": array_factor,
        "truth_extra_load_w": extra_load,
        "truth_p_available_w": p_avail,
    }


def expected_load(telemetry: dict, loads: dict) -> np.ndarray:
    """Load the twin expects from the reported modes (transmitter on, safe mode)."""
    base_w = sum(v for k, v in loads.items() if k not in SHED_IN_SAFE_MODE)
    payload_w, tx_w = loads.get("payload", 0.0), loads.get("radio_tx", 0.0)
    return base_w + np.where(telemetry["safe"], 0.0, payload_w) + tx_w * telemetry["tx_on"]
