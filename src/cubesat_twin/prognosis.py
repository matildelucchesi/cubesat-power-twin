"""Prognosis: from the synchronised state, simulate forward and predict when
the battery will reach the safe-mode threshold, then compare actions.

The twin is started from what the filters know now (state of charge, battery
capacity, array factor, load bias, each with its uncertainty) and from the
recent trend of the parameters (a degrading array or a fading battery). A
Monte Carlo ensemble of forward runs on the predicted orbit turns those
uncertainties into a distribution of the time left before safe mode.

The same ensemble, rerun with a lower transmitter duty cycle, tells the
operator how much margin each option buys: the decision step of
detection -> diagnosis -> prognosis -> action.
"""
import numpy as np

from loads import SHED_IN_SAFE_MODE

TREND_SPAN_H = 72.0
MIN_TREND_SD = {"array": 0.004, "capacity": 0.004}  # per day, floor for model error
DUTY_OPTIONS = (0.5, 0.25, 0.1, 0.0)
MAX_RISK = 0.1          # an action is acceptable if P(safe mode within horizon) <= this


def _trend(values: np.ndarray, hours: np.ndarray, span_h: float) -> tuple[float, float]:
    """Slope per day and its standard error over the last span_h hours, on
    hourly means (the filter output is strongly autocorrelated sample to sample)."""
    sel = hours >= hours[-1] - span_h
    h, v = hours[sel], values[sel]
    bins = np.floor(h).astype(int)
    hb = np.array([h[bins == b].mean() for b in np.unique(bins)]) / 24
    vb = np.array([v[bins == b].mean() for b in np.unique(bins)])
    if len(hb) < 4:
        return 0.0, 0.0
    A = np.column_stack([hb - hb.mean(), np.ones_like(hb)])
    coef, res, *_ = np.linalg.lstsq(A, vb, rcond=None)
    dof = max(len(hb) - 2, 1)
    sigma2 = float(res[0]) / dof if len(res) else 0.0
    se = np.sqrt(sigma2 / ((hb - hb.mean()) ** 2).sum())
    return float(coef[0]), float(se)


def _load(loads: dict, duty: float) -> float:
    base = sum(v for k, v in loads.items() if k not in SHED_IN_SAFE_MODE)
    return base + loads.get("payload", 0.0) + loads.get("radio_tx", 0.0) * duty


def forecast(df, config: dict, est: dict, n_now: int, horizon_days: float,
             n_samples: int = 200, tx_duty: float | None = None, seed: int = 0) -> dict:
    """Ensemble forecast of the state of charge from now (index n_now) for
    horizon_days. est holds the filter outputs up to now, shape (1, n_now)."""
    rng = np.random.default_rng(seed)
    step_s = config["simulation"]["step_s"]
    dt_h = step_s / 3600
    hours = np.arange(n_now) * dt_h
    n_fut = int(horizon_days * 86400 / step_s)
    p_twin = df.power_w.to_numpy()[n_now:n_now + n_fut]
    n_fut = len(p_twin)
    bat, loads = config["battery"], config["loads"]
    duty = loads.get("radio_tx_duty_cycle", 0.5) if tx_duty is None else tx_duty
    enter = config.get("safe_mode", {}).get("enter_soc", 0.4)

    af, af_sd = est["array_factor"][0, -1], est["array_factor_sd"][0, -1]
    cap, cap_sd = est["capacity_wh"][0, -1], est["capacity_sd"][0, -1]
    soc0, soc_sd = est["soc"][0, -1], est["soc_sd"][0, -1]
    load_w = _load(loads, duty) + est["load_bias_w"][0, -1]
    af_slope, af_se = _trend(est["array_factor"][0], hours, TREND_SPAN_H)
    cap_slope, cap_se = _trend(est["capacity_wh"][0] / bat["capacity_wh"], hours, TREND_SPAN_H)
    af_se, cap_se = max(af_se, MIN_TREND_SD["array"]), max(cap_se, MIN_TREND_SD["capacity"])

    # Ensemble. Degradation does not reverse, so sampled slopes are capped at zero.
    a0 = np.clip(rng.normal(af, af_sd, n_samples), 0, 1.5)
    a1 = np.minimum(rng.normal(af_slope, af_se, n_samples), 0)
    c0 = np.clip(rng.normal(cap, cap_sd, n_samples), 0.1, None)
    c1 = np.minimum(rng.normal(cap_slope, cap_se, n_samples), 0) * bat["capacity_wh"]
    s0 = np.clip(rng.normal(soc0, soc_sd, n_samples), 0, 1)

    ch, dis = bat.get("charge_efficiency", 0.9), bat.get("discharge_efficiency", 0.95)
    energy = s0 * c0
    soc = np.empty((n_samples, n_fut))
    cross = np.full(n_samples, np.nan)
    for k in range(n_fut):
        t_d = k * dt_h / 24
        cap_k = np.maximum(c0 + c1 * t_d, 0.1)
        net = np.maximum(a0 + a1 * t_d, 0) * p_twin[k] - load_w
        energy = energy + np.where(net >= 0, net * ch, net / dis) * dt_h
        energy = np.clip(energy, 0, cap_k)
        soc[:, k] = energy / cap_k
        new = np.isnan(cross) & (soc[:, k] < enter)
        cross[new] = k * dt_h

    crossed = ~np.isnan(cross)
    step = max(1, n_fut // 400)
    pct = lambda q: np.round(np.percentile(soc[:, ::step], q, axis=0), 4).tolist()
    tq = (lambda q: round(float(np.percentile(cross[crossed], q)), 1)) if crossed.any() else (lambda q: None)
    return {
        "horizon_days": horizon_days,
        "tx_duty": duty,
        "hours": np.round(np.arange(0, n_fut, step) * dt_h, 3).tolist(),
        "soc_p10": pct(10), "soc_p50": pct(50), "soc_p90": pct(90),
        "threshold_soc": enter,
        "p_safe_mode": round(float(crossed.mean()), 3),
        "safe_mode_h_p10": tq(10), "safe_mode_h_p50": tq(50), "safe_mode_h_p90": tq(90),
        "inputs": {"array_factor": round(float(af), 3), "array_trend_per_day": round(af_slope, 4),
                   "capacity_wh": round(float(cap), 2),
                   "capacity_trend_per_day": round(cap_slope * bat["capacity_wh"], 3),
                   "load_w": round(float(load_w), 3), "soc": round(float(soc0), 3)},
    }


def action_study(df, config: dict, est: dict, n_now: int, horizon_days: float,
                 n_samples: int = 200) -> list[dict]:
    """Forecast for each transmitter duty cycle and flag the recommended one:
    the highest duty cycle (most data downlinked) whose risk of safe mode
    within the horizon stays below MAX_RISK."""
    current = config["loads"].get("radio_tx_duty_cycle", 0.5)
    options = sorted({current, *[d for d in DUTY_OPTIONS if d < current]}, reverse=True)
    rows = []
    for duty in options:
        fc = forecast(df, config, est, n_now, horizon_days, n_samples, tx_duty=duty)
        rows.append({"tx_duty": duty, "p_safe_mode": fc["p_safe_mode"],
                     "safe_mode_h_p50": fc["safe_mode_h_p50"],
                     "min_soc_p50": round(min(fc["soc_p50"]), 3),
                     "downlink_relative": round(duty / current, 2) if current else None})
    ok = [r for r in rows if r["p_safe_mode"] <= MAX_RISK]
    best = ok[0] if ok else rows[-1]
    for r in rows:
        r["recommended"] = r is best
    return rows
