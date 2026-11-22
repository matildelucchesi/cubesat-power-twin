"""Keeping the twin synchronised with telemetry: state and parameter estimation.

The open-loop twin starts from datasheet values and drifts away from the real
spacecraft. Here it is corrected sample by sample with Kalman filters, which
also estimate quantities that no sensor measures:

* battery: an extended Kalman filter (EKF) with state [SOC, 1/capacity].
  The battery current integrated over time (coulomb counting) predicts how
  the SOC moves; the voltage, through the open-circuit-voltage curve, tells
  where the SOC really is. The mismatch between the two reveals the capacity.
* solar array: a scalar Kalman filter on the ratio between measured and
  twin-predicted array power, the *array factor* (1 = as designed). Only
  samples where the array is not curtailed are used: with a full battery the
  power system takes from the array just what the loads draw, and the
  measurement says nothing about what the array could give.
* loads: a scalar Kalman filter on the difference between the measured load
  and the load the twin expects from the reported modes.

All filters run on a batch of scenarios at once (arrays of shape (S, n)).
Each estimate comes with its standard deviation.
"""
import numpy as np

from telemetry import docv_dsoc, expected_load, ocv, R_INTERNAL_OHM, NOISE

FULL_SOC = 0.97   # above this the battery may be full and the array curtailed


def array_usable(telemetry: dict, sunlit, r_int: float = R_INTERNAL_OHM) -> np.ndarray:
    """Samples where the array power measures what the array can produce: in
    sunlight and with the battery clearly below full charge. The SOC test
    uses the voltage corrected for the internal resistance, which near full
    charge rises by about 20 mV per percent of SOC. Whole full-battery
    periods are skipped, not just the curtailed samples, so the selection
    does not favour the moments of low illumination."""
    v_oc = telemetry["v_batt_v"] - r_int * telemetry["i_batt_a"]
    return np.asarray(sunlit, dtype=bool)[None, :] & (v_oc < ocv(FULL_SOC))


def battery_ekf(telemetry: dict, step_s: float, capacity_wh: float,
                charge_eff: float = 0.9, discharge_eff: float = 0.95,
                r_int: float = R_INTERNAL_OHM) -> dict[str, np.ndarray]:
    """EKF on [SOC, q = 1/capacity]. Returns soc, soc_sd, capacity_wh, capacity_sd."""
    v, i = telemetry["v_batt_v"], telemetry["i_batt_a"]
    S, n = v.shape
    dt_h = step_s / 3600
    # Initial SOC from the first voltage sample; capacity from the datasheet.
    x = np.stack([np.interp(v[:, 0] - r_int * i[:, 0], ocv(np.linspace(0, 1, 101)),
                            np.linspace(0, 1, 101)),
                  np.full(S, 1 / capacity_wh)], axis=1)
    P = np.zeros((S, 2, 2))
    P[:, 0, 0], P[:, 1, 1] = 0.05 ** 2, (0.15 / capacity_wh) ** 2
    Q = np.diag([1e-6, (5e-4 / capacity_wh) ** 2])  # process noise per step
    r_meas = NOISE["v_batt_v"] ** 2 + 0.01 ** 2       # voltage noise plus model error

    out = {k: np.empty((S, n)) for k in ("soc", "soc_sd", "capacity_wh", "capacity_sd")}
    for k in range(n):
        if k > 0:
            e = v[:, k - 1] * i[:, k - 1] * dt_h                      # energy into the battery [Wh]
            e = np.where(e >= 0, e * charge_eff, e / discharge_eff)
            x[:, 0] = np.clip(x[:, 0] + e * x[:, 1], 0, 1)
            F = np.zeros((S, 2, 2))
            F[:, 0, 0], F[:, 0, 1], F[:, 1, 1] = 1.0, e, 1.0
            P = F @ P @ F.transpose(0, 2, 1) + Q
        # Measurement update with the battery voltage.
        h = ocv(x[:, 0]) + r_int * i[:, k]
        H = np.stack([docv_dsoc(x[:, 0]), np.zeros(S)], axis=1)       # (S, 2)
        s = np.einsum("si,sij,sj->s", H, P, H) + r_meas
        K = np.einsum("sij,sj->si", P, H) / s[:, None]
        x = x + K * (v[:, k] - h)[:, None]
        x[:, 0] = np.clip(x[:, 0], 0, 1)
        x[:, 1] = np.clip(x[:, 1], 1 / (5 * capacity_wh), 1 / (0.1 * capacity_wh))
        P = P - np.einsum("si,sj->sij", K, H) @ P
        out["soc"][:, k], out["soc_sd"][:, k] = x[:, 0], np.sqrt(P[:, 0, 0])
        out["capacity_wh"][:, k] = 1 / x[:, 1]
        out["capacity_sd"][:, k] = np.sqrt(P[:, 1, 1]) / x[:, 1] ** 2
    return out


def scalar_kf(y: np.ndarray, h: np.ndarray, mask: np.ndarray, r: np.ndarray,
              q: float, x0: float, p0: float) -> tuple[np.ndarray, np.ndarray]:
    """Random-walk Kalman filter for a parameter theta with y = h * theta + noise,
    updated only where mask is True. Returns (estimate, sd), shape (S, n)."""
    S, n = y.shape
    x, p = np.full(S, x0, dtype=float), np.full(S, p0, dtype=float)
    est, sd = np.empty((S, n)), np.empty((S, n))
    for k in range(n):
        p = p + q
        m = mask[:, k]
        hk = h[:, k]
        s = hk * hk * p + r[:, k]
        gain = np.where(m, p * hk / s, 0.0)
        x = x + gain * (y[:, k] - hk * x)
        p = p - gain * hk * p
        est[:, k], sd[:, k] = x, np.sqrt(p)
    return est, sd


def synchronise(telemetry: dict, twin_power_w, sunlit, config: dict) -> dict[str, np.ndarray]:
    """Run all the filters. twin_power_w is the twin's predicted array power
    (orbit-average model, as-designed array)."""
    step_s = config["simulation"]["step_s"]
    bat = config["battery"]
    est = battery_ekf(telemetry, step_s, bat["capacity_wh"],
                      bat.get("charge_efficiency", 0.9), bat.get("discharge_efficiency", 0.95))

    p_twin = np.broadcast_to(np.asarray(twin_power_w, dtype=float), telemetry["p_array_w"].shape)
    usable = array_usable(telemetry, sunlit) & (p_twin > 0.05)
    # Tumbling makes the instantaneous array power swing around the average:
    # that is the dominant "noise" of this measurement (about 35 % of the mean).
    r_arr = (0.35 * p_twin) ** 2 + NOISE["p_array_w"] ** 2
    est["array_factor"], est["array_factor_sd"] = scalar_kf(
        telemetry["p_array_w"], p_twin, usable, r_arr, q=(5e-4) ** 2, x0=1.0, p0=0.1 ** 2)
    est["array_usable"] = usable

    resid = telemetry["p_load_w"] - expected_load(telemetry, config["loads"])
    ones = np.ones_like(resid)
    est["load_bias_w"], est["load_bias_sd"] = scalar_kf(
        resid, ones, np.ones_like(resid, dtype=bool), ones * NOISE["p_load_w"] ** 2,
        q=(1e-3) ** 2, x0=0.0, p0=0.2 ** 2)
    return est
