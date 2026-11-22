"""Health monitoring pipeline: telemetry -> synchronised twin -> detection,
diagnosis and prognosis.

    python src/cubesat_twin/health.py [fault] [magnitude]

runs one scenario end to end and prints the report. The API's /api/health
endpoint and the dashboard's Health panel use run_health().
"""
import copy
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from detection import (FEATURES, SUBSYSTEM, cusum_check, hybrid_check, limit_check, ml_check,
                       window_features)
from estimation import synchronise
from simulation import load_config, run
from telemetry import FAULT_KINDS, Scenario, generate

CONFIG_PATH = "configs/cubesat_1u.yaml"
MODEL_PATH = Path("results/models/diagnosis.joblib")

# Ranges of fault sizes used for training and benchmarking.
MAGNITUDE_RANGE = {
    "string_loss": (0.5, 1.0),          # fraction of one face's cells
    "array_degradation": (0.01, 0.04),  # output lost per day
    "capacity_fade": (0.01, 0.04),      # capacity lost per day
    "load_anomaly": (0.15, 0.5),        # extra watts
}
DEFAULT_MAGNITUDE = {"nominal": 0.0, "string_loss": 1.0, "array_degradation": 0.03,
                     "capacity_fade": 0.03, "load_anomaly": 0.3}


def base_config(days: float, step_s: int = 60, path: str = CONFIG_PATH) -> dict:
    """The 1U configuration with safe mode on, as flown by the health scenarios."""
    cfg = copy.deepcopy(load_config(path))
    cfg["simulation"].update(duration_days=days, step_s=step_s, eclipse_model="cylindrical")
    cfg["safe_mode"] = {"enabled": True, "enter_soc": 0.4, "exit_soc": 0.6}
    return cfg


def twin_run(config: dict) -> pd.DataFrame:
    """Open-loop twin: orbit, eclipses and orbit-average array power."""
    return run(config)


def random_scenarios(n_per_kind: int, days: float, rng: np.random.Generator,
                     seed_offset: int = 0) -> list[Scenario]:
    out = []
    for kind in FAULT_KINDS:
        for _ in range(n_per_kind):
            seed = seed_offset + len(out)
            if kind == "nominal":
                out.append(Scenario("nominal", onset_h=float("inf"), seed=seed))
                continue
            onset = rng.uniform(CALIB_END_H + 6, days * 24 - 48)
            out.append(Scenario(kind, onset, float(rng.uniform(*MAGNITUDE_RANGE[kind])), seed))
    return out


CALIB_END_H = 48.0


def analyse(df: pd.DataFrame, config: dict, scenarios: list[Scenario], tx_duty=None):
    """Telemetry, synchronised estimates and window features for a batch."""
    tm = generate(df, config, scenarios, tx_duty=tx_duty)
    est = synchronise(tm, df.power_w.to_numpy(), df.sunlit, config)
    f = window_features(tm, est, df.power_w.to_numpy(), config["battery"]["capacity_wh"],
                        config["simulation"]["step_s"])
    return tm, est, f


@lru_cache
def classifier():
    """The trained diagnosis model: loaded from disk, or trained once on a
    small synthetic set if the benchmark has not been run yet."""
    import joblib

    if MODEL_PATH.exists():
        saved = joblib.load(MODEL_PATH)
        if isinstance(saved, dict) and saved.get("features") == FEATURES:
            return saved["model"]
    from health_benchmark import run_benchmark

    _, clf = run_benchmark(n_per_kind=12)
    save_classifier(clf)
    return clf


def save_classifier(clf) -> None:
    """Store the model with its feature list, so a model trained on other
    features is never loaded by mistake."""
    import joblib

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"features": FEATURES, "model": clf}, MODEL_PATH)


def run_health(scenario: Scenario, days: float = 10, horizon_days: float = 10,
               tx_duty: float | None = None, n_samples: int = 200, clf=None,
               return_context: bool = False):
    """One scenario end to end. The telemetry covers `days`; the truth model
    keeps running for `horizon_days` more, so the prognosis made at the end
    of the telemetry can be checked against what really happens."""
    from prognosis import action_study, forecast

    config = base_config(days + horizon_days)
    if tx_duty is not None:
        config["loads"]["radio_tx_duty_cycle"] = tx_duty
    df = twin_run(config)
    tm, est, f = analyse(df, config, [scenario])
    step_s = config["simulation"]["step_s"]
    n_now = int(days * 86400 / step_s)

    # Detection and diagnosis use only the telemetry received so far.
    past = {k: (v[:, :n_now] if isinstance(v, np.ndarray) and v.ndim == 2 else v)
            for k, v in tm.items()}
    past["hours"] = tm["hours"][:n_now]
    est_past = {k: v[:, :n_now] for k, v in est.items()}
    f_past = window_features(past, est_past, df.power_w.to_numpy()[:n_now],
                             config["battery"]["capacity_wh"], step_s)
    clf = clf or classifier()
    alarms = [limit_check(f_past)[0], cusum_check(f_past)[0]]
    ml_alarms, proba = ml_check(clf, f_past)
    alarms += [ml_alarms[0], hybrid_check(clf, f_past, proba)[0]]

    fc = forecast(df, config, est_past, n_now, horizon_days, n_samples=n_samples)
    actions = action_study(df, config, est_past, n_now, horizon_days, n_samples=n_samples)
    truth_entry = np.flatnonzero(tm["safe"][0, n_now:])
    actual_h = float(tm["hours"][n_now + truth_entry[0]] - tm["hours"][n_now]) if len(truth_entry) else None

    step = max(1, n_now // 1500)
    s = slice(0, n_now, step)
    report = {
        "scenario": {"kind": scenario.kind, "onset_h": scenario.onset_h if np.isfinite(scenario.onset_h) else None,
                     "magnitude": scenario.magnitude, "seed": scenario.seed,
                     "failed_face": scenario.failed_face, "subsystem": SUBSYSTEM[scenario.kind]},
        "days": days,
        "series": {
            "hours": np.round(tm["hours"][s], 3).tolist(),
            "p_array_w": np.round(tm["p_array_w"][0, s], 3).tolist(),
            "p_twin_w": np.round(df.power_w.to_numpy()[s], 3).tolist(),
            "p_load_w": np.round(tm["p_load_w"][0, s], 3).tolist(),
            "v_batt_v": np.round(tm["v_batt_v"][0, s], 3).tolist(),
            "soc_est": np.round(est["soc"][0, s], 4).tolist(),
            "soc_sd": np.round(est["soc_sd"][0, s], 4).tolist(),
            "soc_true": np.round(tm["truth_soc"][0, s], 4).tolist(),
            "capacity_est": np.round(est["capacity_wh"][0, s], 3).tolist(),
            "capacity_sd": np.round(est["capacity_sd"][0, s], 3).tolist(),
            "capacity_true": np.round(tm["truth_capacity_wh"][0, s], 3).tolist(),
            "array_factor_est": np.round(est["array_factor"][0, s], 4).tolist(),
            "array_factor_sd": np.round(est["array_factor_sd"][0, s], 4).tolist(),
            "array_factor_true": np.round(tm["truth_array_factor"][0, s], 4).tolist(),
            "load_bias_est": np.round(est["load_bias_w"][0, s], 3).tolist(),
            "load_bias_true": np.round(tm["truth_extra_load_w"][0, s], 3).tolist(),
            "safe": tm["safe"][0, s].astype(bool).tolist(),
        },
        "windows": {
            "end_h": np.round(f_past["end_h"], 2).tolist(),
            "fault_probability": np.round(1 - proba[0, :, FAULT_KINDS.index("nominal")], 3).tolist(),
            "probabilities": {k: np.round(proba[0, :, i], 3).tolist() for i, k in enumerate(FAULT_KINDS)},
        },
        "estimates_now": {
            "soc": round(float(est["soc"][0, n_now - 1]), 3),
            "soc_sd": round(float(est["soc_sd"][0, n_now - 1]), 3),
            "capacity_wh": round(float(est["capacity_wh"][0, n_now - 1]), 2),
            "capacity_sd": round(float(est["capacity_sd"][0, n_now - 1]), 2),
            "capacity_datasheet_wh": config["battery"]["capacity_wh"],
            "array_factor": round(float(est["array_factor"][0, n_now - 1]), 3),
            "array_factor_sd": round(float(est["array_factor_sd"][0, n_now - 1]), 3),
            "load_bias_w": round(float(est["load_bias_w"][0, n_now - 1]), 3),
            "truth": {
                "soc": round(float(tm["truth_soc"][0, n_now - 1]), 3),
                "capacity_wh": round(float(tm["truth_capacity_wh"][0, n_now - 1]), 2),
                "array_factor": round(float(tm["truth_array_factor"][0, n_now - 1]), 3),
                "extra_load_w": round(float(tm["truth_extra_load_w"][0, n_now - 1]), 3),
            },
        },
        "alarms": [{"method": a.method, "hour": a.hour, "subsystem": a.subsystem,
                    "fault": a.fault, "channel": a.channel,
                    "delay_h": (round(a.hour - scenario.onset_h, 1)
                                if a.hour is not None and np.isfinite(scenario.onset_h) else None)}
                   for a in alarms],
        "features_now": {name: round(float(f_past[name][0, -1]), 4) for name in FEATURES},
        "prognosis": {**fc, "actual_safe_mode_h": actual_h},
        "actions": actions,
    }
    if not return_context:
        return report

    def what_if(duty: float) -> dict:
        r = forecast(df, config, est_past, n_now, horizon_days, n_samples, tx_duty=min(max(duty, 0), 1))
        return {"tx_duty": r["tx_duty"], "p_safe_mode": r["p_safe_mode"],
                "safe_mode_h_p50": r["safe_mode_h_p50"], "min_soc_p50": round(min(r["soc_p50"]), 3)}

    return report, what_if


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "array_degradation"
    mag = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAGNITUDE[kind]
    rep = run_health(Scenario(kind, onset_h=72, magnitude=mag, seed=7))
    print("scenario:", rep["scenario"])
    print("estimates now:", rep["estimates_now"])
    for a in rep["alarms"]:
        print("alarm:", a)
    p = {k: v for k, v in rep["prognosis"].items() if k not in ("hours", "soc_p10", "soc_p50", "soc_p90")}
    print("prognosis:", p)
    for a in rep["actions"]:
        print("action:", a)
