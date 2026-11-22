"""Fault detection and diagnosis: limit checking against the twin.

Three methods are compared on the same telemetry:

1. limit checking, the classic operations approach: an alarm when a
   telemetry channel crosses a fixed limit (low battery voltage, high load);
2. twin residuals + CUSUM: the synchronised twin's parameter estimates
   (array factor, capacity, load bias) are compared with their values in a
   calibration period, and a cumulative-sum test raises an alarm when the
   drift is persistent. The channel that alarms names the subsystem;
3. twin features + machine learning: a random forest classifies each orbit
   from the same estimates and their trends, and names the fault type
   (for example a lost cell string versus gradual degradation, which look
   the same to a threshold on one channel);
4. hybrid: the CUSUM decides *when* (it raises few false alarms) and the
   classifier decides *what*, at the moment of the alarm.

Everything is computed per orbit-long window. The first CALIBRATION_H hours
of each run are the calibration period: the twin learns what "normal" looks
like for this spacecraft, whose real parameters differ from the datasheet.
"""
from dataclasses import dataclass

import numpy as np

from telemetry import FAULT_KINDS, ocv

ORBIT_MIN = 95.5
CALIBRATION_H = (12.0, 48.0)

# Operations limits for the 1U power system.
LIMITS = {"v_batt_low_v": float(ocv(0.45)), "p_load_high_w": 2.5}

SUBSYSTEM = {"nominal": "none", "string_loss": "array", "array_degradation": "array",
             "capacity_fade": "battery", "load_anomaly": "loads"}

FEATURES = ["array_z", "array_slope_24h", "array_slope_72h", "array_spread_dev",
            "capacity_z", "capacity_slope_48h", "load_z", "array_dev", "capacity_dev"]

CUSUM_K, CUSUM_H = 1.0, 8.0          # allowance and decision threshold, in sigmas
CUSUM_FLOOR = {"array": 0.03, "battery": 0.03, "loads": 0.03}  # model error floors
ML_PROB, ML_PERSIST = 0.8, 4          # probability and consecutive windows for an ML alarm


def window_ends(n_steps: int, step_s: float) -> np.ndarray:
    w = max(1, int(round(ORBIT_MIN * 60 / step_s)))
    return np.arange(w - 1, n_steps, w)


def _slope(values: np.ndarray, hours: np.ndarray, ends: np.ndarray, span_h: float) -> np.ndarray:
    """Least-squares slope per day of values over the span_h hours before each window end."""
    out = np.zeros((values.shape[0], len(ends)))
    for j, e in enumerate(ends):
        start = np.searchsorted(hours, hours[e] - span_h)
        t = hours[start:e + 1] / 24
        if len(t) < 3:
            continue
        t = t - t.mean()
        y = values[:, start:e + 1]
        out[:, j] = (y - y.mean(axis=1, keepdims=True)) @ t / (t @ t)
    return out


def _array_spread(telemetry: dict, est: dict, twin_power, ends: np.ndarray, span_h: float = 24) -> np.ndarray:
    """Spread (std/mean) of measured over predicted array power on usable
    samples, over the last span_h hours. Tumbling makes this spread a
    signature of the array geometry: losing one face changes it, while
    uniform degradation only rescales the power."""
    hours = telemetry["hours"]
    ratio = telemetry["p_array_w"] / np.maximum(np.broadcast_to(twin_power, telemetry["p_array_w"].shape), 1e-3)
    usable = est["array_usable"] & (np.broadcast_to(twin_power, ratio.shape) > 0.05)
    out = np.full((ratio.shape[0], len(ends)), np.nan)
    for j, e in enumerate(ends):
        start = np.searchsorted(hours, hours[e] - span_h)
        r, m = ratio[:, start:e + 1], usable[:, start:e + 1]
        cnt = m.sum(axis=1)
        mean = np.where(cnt > 0, (r * m).sum(axis=1) / np.maximum(cnt, 1), np.nan)
        var = np.where(cnt > 1, ((r - mean[:, None]) ** 2 * m).sum(axis=1) / np.maximum(cnt - 1, 1), np.nan)
        out[:, j] = np.sqrt(var) / mean
    return out


def window_features(telemetry: dict, est: dict, twin_power, capacity_nominal: float,
                    step_s: float) -> dict[str, np.ndarray]:
    """Per-window features, each of shape (S, n_windows), plus the window end hours."""
    hours = telemetry["hours"]
    ends = window_ends(len(hours), step_s)
    end_h = hours[ends]
    calib = (end_h >= CALIBRATION_H[0]) & (end_h <= CALIBRATION_H[1])

    af = est["array_factor"][:, ends]
    cap = est["capacity_wh"][:, ends] / capacity_nominal
    spread = _array_spread(telemetry, est, twin_power, ends)

    def dev(x):
        base = np.nanmean(x[:, calib], axis=1, keepdims=True)
        return x - base

    spread_dev = np.nan_to_num(dev(spread), nan=0.0)
    array_sd = est["array_factor_sd"][:, ends]
    capacity_sd = est["capacity_sd"][:, ends] / capacity_nominal
    load_bias, load_sd = est["load_bias_w"][:, ends], est["load_bias_sd"][:, ends]
    array_dev, capacity_dev = dev(af), dev(cap)
    load_dev = load_bias - load_bias[:, calib].mean(axis=1, keepdims=True)
    return {
        "end_h": end_h,
        "calibration": calib,
        "array_dev": array_dev,
        "array_z": array_dev / np.sqrt(array_sd ** 2 + CUSUM_FLOOR["array"] ** 2),
        "array_slope_24h": _slope(est["array_factor"], hours, ends, 24),
        "array_slope_72h": _slope(est["array_factor"], hours, ends, 72),
        "array_spread_dev": spread_dev,
        "capacity_dev": capacity_dev,
        "capacity_z": capacity_dev / np.sqrt(capacity_sd ** 2 + CUSUM_FLOOR["battery"] ** 2),
        "load_z": load_dev / np.sqrt(load_sd ** 2 + CUSUM_FLOOR["loads"] ** 2),
        "capacity_slope_48h": _slope(est["capacity_wh"] / capacity_nominal, hours, ends, 48),
        "load_bias": load_bias,
        "array_sd": array_sd,
        "capacity_sd": capacity_sd,
        "load_bias_sd": load_sd,
        "v_min": np.minimum.reduceat(telemetry["v_batt_v"], np.r_[0, ends[:-1] + 1], axis=1),
        "p_load_max": np.maximum.reduceat(telemetry["p_load_w"], np.r_[0, ends[:-1] + 1], axis=1),
    }


def feature_matrix(f: dict) -> np.ndarray:
    """(S * n_windows, n_features) matrix for the classifier."""
    return np.stack([f[name] for name in FEATURES], axis=-1).reshape(-1, len(FEATURES))


def window_labels(f: dict, scenarios) -> np.ndarray:
    """Ground-truth label of each window: the fault kind once it has started."""
    return np.array([[sc.kind if h >= sc.onset_h else "nominal" for h in f["end_h"]]
                     for sc in scenarios])


@dataclass
class Alarm:
    method: str
    hour: float | None          # time of the first alarm, None if no alarm
    subsystem: str              # array | battery | loads | unknown | none
    fault: str | None = None    # exact fault type, when the method can tell
    channel: str | None = None


def limit_check(f: dict) -> list[Alarm]:
    """First window where a fixed limit is crossed, per scenario."""
    out = []
    after = f["end_h"] > CALIBRATION_H[1]
    for s in range(f["v_min"].shape[0]):
        v = (f["v_min"][s] < LIMITS["v_batt_low_v"]) & after
        p = (f["p_load_max"][s] > LIMITS["p_load_high_w"]) & after
        hits = [(np.argmax(x), name) for x, name in ((p, "p_load"), (v, "v_batt")) if x.any()]
        if not hits:
            out.append(Alarm("limit", None, "none"))
            continue
        j, name = min(hits)
        out.append(Alarm("limit", float(f["end_h"][j]), "loads" if name == "p_load" else "unknown",
                         "load_anomaly" if name == "p_load" else None, name))
    return out


def cusum(z: np.ndarray, k: float = CUSUM_K) -> np.ndarray:
    """One-sided upper CUSUM statistic of a standardised series, along the last axis."""
    g = np.zeros_like(z)
    acc = np.zeros(z.shape[:-1])
    for j in range(z.shape[-1]):
        acc = np.maximum(0.0, acc + z[..., j] - k)
        g[..., j] = acc
    return g


def cusum_check(f: dict) -> list[Alarm]:
    """CUSUM on the twin's parameter drifts since the calibration period.
    Each drift is divided by the filter's own standard deviation, with a
    floor for model error, so that a drift counts only when it is larger
    than what the twin can resolve. Low array factor, low capacity and high
    load bias are the directions that matter."""
    calib, after = f["calibration"], f["end_h"] > CALIBRATION_H[1]
    channels = {
        "array": (-f["array_dev"], f["array_sd"], CUSUM_FLOOR["array"]),
        "battery": (-f["capacity_dev"], f["capacity_sd"], CUSUM_FLOOR["battery"]),
        "loads": (f["load_bias"] - f["load_bias"][:, calib].mean(axis=1, keepdims=True),
                  f["load_bias_sd"], CUSUM_FLOOR["loads"]),
    }
    stats = {}
    for name, (x, sd, floor) in channels.items():
        z = np.where(after, x / np.sqrt(sd ** 2 + floor ** 2), 0.0)
        stats[name] = cusum(z)
    out = []
    for s in range(f["array_dev"].shape[0]):
        hits = [(np.argmax(g[s] > CUSUM_H), name) for name, g in stats.items() if (g[s] > CUSUM_H).any()]
        if not hits:
            out.append(Alarm("twin_cusum", None, "none"))
            continue
        j, name = min(hits)
        out.append(Alarm("twin_cusum", float(f["end_h"][j]), name, channel=name))
    return out


def train_classifier(features: list[dict], labels: list[np.ndarray], seed: int = 0):
    """Random forest on the window features. Calibration windows and the
    first hours of each run are left out: the twin is still converging."""
    from sklearn.ensemble import RandomForestClassifier

    X, y = [], []
    for f, lab in zip(features, labels):
        keep = np.broadcast_to(f["end_h"] > CALIBRATION_H[0], lab.shape).reshape(-1)
        X.append(feature_matrix(f)[keep])
        y.append(lab.reshape(-1)[keep])
    clf = RandomForestClassifier(n_estimators=200, max_depth=10, min_samples_leaf=5,
                                 class_weight="balanced", random_state=seed, n_jobs=-1)
    clf.fit(np.concatenate(X), np.concatenate(y))
    return clf


def ml_probabilities(clf, f: dict) -> np.ndarray:
    """Class probabilities per window, shape (S, n_windows, n_classes) in FAULT_KINDS order."""
    S, W = f["array_dev"].shape
    proba = clf.predict_proba(feature_matrix(f))
    out = np.zeros((S * W, len(FAULT_KINDS)))
    for i, c in enumerate(clf.classes_):
        out[:, FAULT_KINDS.index(c)] = proba[:, i]
    return out.reshape(S, W, len(FAULT_KINDS))


def ml_check(clf, f: dict) -> tuple[list[Alarm], np.ndarray]:
    """Alarm when the probability of a fault stays above ML_PROB for
    ML_PERSIST consecutive windows; the diagnosis is the most probable
    fault over those windows."""
    proba = ml_probabilities(clf, f)
    p_fault = 1 - proba[..., FAULT_KINDS.index("nominal")]
    after = f["end_h"] > CALIBRATION_H[1]
    out = []
    for s in range(proba.shape[0]):
        hit = (p_fault[s] > ML_PROB) & after
        run = np.convolve(hit.astype(int), np.ones(ML_PERSIST, dtype=int), mode="full")[:len(hit)]
        idx = np.flatnonzero(run >= ML_PERSIST)
        if not len(idx):
            out.append(Alarm("twin_ml", None, "none"))
            continue
        j = idx[0]
        mean = proba[s, j - ML_PERSIST + 1:j + 1].mean(axis=0)
        mean[FAULT_KINDS.index("nominal")] = 0
        fault = FAULT_KINDS[int(mean.argmax())]
        out.append(Alarm("twin_ml", float(f["end_h"][j]), SUBSYSTEM[fault], fault))
    return out, proba


def hybrid_check(clf, f: dict, proba: np.ndarray | None = None) -> list[Alarm]:
    """Detection by the twin CUSUM (few false alarms), diagnosis by the
    classifier at the moment of the alarm: the fault type with the highest
    mean probability over the last ML_PERSIST windows, restricted to the
    subsystem the CUSUM pointed to when the classifier disagrees."""
    proba = ml_probabilities(clf, f) if proba is None else proba
    out = []
    for s, a in enumerate(cusum_check(f)):
        if a.hour is None:
            out.append(Alarm("twin_hybrid", None, "none"))
            continue
        j = int(np.searchsorted(f["end_h"], a.hour))
        mean = proba[s, max(0, j - ML_PERSIST + 1):j + 1].mean(axis=0)
        mean[FAULT_KINDS.index("nominal")] = 0
        same = [i for i, k in enumerate(FAULT_KINDS) if SUBSYSTEM[k] == a.subsystem]
        if SUBSYSTEM[FAULT_KINDS[int(mean.argmax())]] != a.subsystem and same:
            fault = FAULT_KINDS[max(same, key=lambda i: mean[i])]
        else:
            fault = FAULT_KINDS[int(mean.argmax())]
        out.append(Alarm("twin_hybrid", a.hour, SUBSYSTEM[fault], fault, a.channel))
    return out
