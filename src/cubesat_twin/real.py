"""Real-data mode: the twin aligned with FUNcube-1 (AO-73) in orbit.

The simulated mode flies a generic 1U CubeSat on FUNcube-1's orbit. This
mode compares that twin with the housekeeping telemetry FUNcube-1 actually
sent (whole-orbit buffer, one sample per minute, received by SatNOGS ground
stations; see satnogs.py and funcube.py), and synchronises it:

1. **Time and eclipses.** The orbit comes from the TLE (SGP4). Each eclipse
   edge seen in the telemetry (photocurrent dropping to zero or coming back)
   is compared with the edge the twin predicts. The mean difference is the
   unknown phase of the on-board sampling (a constant time offset, applied
   from then on); the spread says how well the twin times the eclipses.
2. **Solar array.** A Kalman filter on measured / predicted array power
   gives the *array factor*: how the real array compares with the generic
   as-designed array of the twin (cell area, efficiency, attitude, and 13
   years of degradation all folded into one number).
3. **Loads.** Kalman filters on the system power in sunlight and in eclipse
   (FUNcube-1 switches its telemetry transmitter off in eclipse).
4. **Battery.** FUNcube-1's power system stops charging at a fixed voltage
   and dumps the surplus, so most of the orbit the battery is full. The
   state is the *charge deficit* D [Ah] below full, from coulomb counting of
   the measured currents. A Kalman filter estimates the battery voltage
   model V = V_full - k D + R I: the full-charge voltage, the voltage drop
   per ampere-hour drawn (k), and the internal resistance (R). None of them
   is measured. With a typical Li-ion open-circuit curve, k gives the
   capacity.

The parameters are estimated on the first half of the data (calibration)
and the synchronised twin then *predicts* the second half from the orbit
alone: only the first voltage of each pass is used to set the initial
state. The same prediction with the generic datasheet twin is the baseline.

    python src/cubesat_twin/real.py        # summary + docs/real-vs-twin.png
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from skyfield.api import EarthSatellite, load

from eclipse import sunlit_cylindrical
from estimation import scalar_kf
from loads import average_load
from orbit import read_tle
from power import effective_area, solar_irradiance
from telemetry import OCV_CELL_V, OCV_SOC, CELLS_IN_SERIES, R_INTERNAL_OHM, docv_dsoc, ocv
from simulation import load_config

DATA = Path("data/real/funcube1_wholeorbit.csv")
TLE_FILE = "data/tle/funcube1.tle"
CONFIG = "configs/cubesat_1u.yaml"
SPLIT = "2026-11-18T00:00Z"   # calibration before, validation after

SUN_CURRENT_MA = 20           # above this the array is lit
EDGE_GUARD_MIN = 2            # minutes around an eclipse edge left out of the array filter
MIN_SEGMENT_MIN = 60          # passes shorter than this are not used for validation
FULL_MARGIN_V = 0.015         # within this of the pass's top voltage, in sunlight: battery full


# ---------------------------------------------------------------- data

def load_telemetry(path: Path = DATA) -> pd.DataFrame:
    """Whole-orbit telemetry in SI units, one row per minute, with pass segments."""
    raw = pd.read_csv(path)
    df = pd.DataFrame({"time": pd.to_datetime(raw.time_utc, utc=True)})
    df["v_batt_v"] = raw.battery_mv / 1000
    df["i_array_a"] = raw.photocurrent_ma / 1000
    df["i_sys_a"] = raw.system_ma / 1000
    # Both currents are measured on the battery bus: power = bus voltage x current.
    df["p_array_w"] = df.v_batt_v * df.i_array_a
    df["p_load_w"] = df.v_batt_v * df.i_sys_a
    df["i_net_a"] = df.i_array_a - df.i_sys_a
    df["lit_meas"] = raw.photocurrent_ma > SUN_CURRENT_MA
    gap = df.time.diff().dt.total_seconds().fillna(1e9) > 61
    df["segment"] = gap.cumsum() - 1
    return df


def unix_seconds(times: pd.Series) -> np.ndarray:
    """UTC seconds since 1970 (independent of the datetime resolution pandas uses)."""
    return (times - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy()


# ---------------------------------------------------------------- twin geometry

_TS = load.timescale()


def satellite(tle_file: str = TLE_FILE) -> EarthSatellite:
    name, l1, l2 = read_tle(tle_file)
    return EarthSatellite(l1, l2, name, _TS)


def twin_sunlit(sat: EarthSatellite, unix_s: np.ndarray) -> np.ndarray:
    """Cylindrical-shadow sunlight flag at the given UTC times."""
    t = _TS.utc(1970, 1, 1, 0, 0, np.asarray(unix_s, dtype=float))
    x, y, z = sat.at(t).position.km
    times = pd.Series(pd.to_datetime(np.asarray(unix_s), unit="s", utc=True))
    return sunlit_cylindrical(pd.DataFrame({"time": times, "x_km": x, "y_km": y, "z_km": z}))


def twin_array_power(times: pd.Series, sunlit, config: dict) -> np.ndarray:
    """As-designed array power of the generic twin [W] (orbit-average attitude model)."""
    arr = config["solar_array"]
    area = effective_area(arr["faces"], arr["attitude"], arr.get("deployable", 0.0))
    return np.asarray(sunlit, float) * solar_irradiance(times) * arr["cell_efficiency"] * area


# ---------------------------------------------------------------- 1. eclipse edges

def measured_edges(df: pd.DataFrame) -> pd.DataFrame:
    """Eclipse entries and exits in the telemetry, timed by linear interpolation
    of the photocurrent through half of its sunlit level."""
    half = 0.5 * df.i_array_a[df.lit_meas].median()
    rows = []
    for _, seg in df.groupby("segment"):
        t = unix_seconds(seg.time)
        i = seg.i_array_a.to_numpy()
        for k in range(len(seg) - 1):
            a, b = i[k] - half, i[k + 1] - half
            if a * b < 0:
                frac = a / (a - b)
                rows.append({"kind": "entry" if a > 0 else "exit", "t_meas": t[k] + frac * 60})
    return pd.DataFrame(rows).sort_values("t_meas", ignore_index=True)


def predicted_edge(sat, t_guess: float, kind: str, span_s: int = 300) -> float | None:
    """Time of the twin's shadow crossing nearest to t_guess, to 1 s."""
    ts = t_guess + np.arange(-span_s, span_s + 1)
    lit = twin_sunlit(sat, ts)
    change = np.flatnonzero(np.diff(lit.astype(int)) == (-1 if kind == "entry" else 1))
    if not len(change):
        return None
    k = change[np.argmin(np.abs(ts[change] - t_guess))]
    return ts[k] + 0.5


def eclipse_timing(df: pd.DataFrame, sat) -> dict:
    """Measured minus predicted eclipse edges. The mean of entries and exits
    is the clock offset of the whole-orbit samples; exit minus entry is the
    error on the eclipse duration."""
    edges = measured_edges(df)
    edges["t_pred"] = [predicted_edge(sat, r.t_meas, r.kind) for r in edges.itertuples()]
    edges = edges.dropna()
    edges["diff_s"] = edges.t_meas - edges.t_pred
    entry, exit_ = edges.diff_s[edges.kind == "entry"], edges.diff_s[edges.kind == "exit"]
    offset = 0.5 * (entry.median() + exit_.median())
    resid = np.where(edges.kind == "entry", edges.diff_s - entry.median(), edges.diff_s - exit_.median())
    return {
        "edges": edges,
        "offset_s": float(offset),
        "entry_minus_pred_s": float(entry.median()),
        "exit_minus_pred_s": float(exit_.median()),
        "duration_error_s": float(exit_.median() - entry.median()),
        "edge_scatter_s": float(np.std(resid)),
        "n_entries": int(len(entry)), "n_exits": int(len(exit_)),
    }


def eclipse_durations(df: pd.DataFrame, sat, offset_s: float) -> dict:
    """Complete eclipses inside one pass: measured and predicted duration [min]."""
    edges = measured_edges(df)
    meas, pred = [], []
    for k in range(len(edges) - 1):
        a, b = edges.iloc[k], edges.iloc[k + 1]
        if a.kind == "entry" and b.kind == "exit" and b.t_meas - a.t_meas < 3000:
            e = predicted_edge(sat, a.t_meas - offset_s, "entry")
            x = predicted_edge(sat, b.t_meas - offset_s, "exit")
            if e and x:
                meas.append((b.t_meas - a.t_meas) / 60)
                pred.append((x - e) / 60)
    return {"measured_min": float(np.mean(meas)), "predicted_min": float(np.mean(pred)),
            "count": len(meas)}


# ---------------------------------------------------------------- 4. battery

def charge_deficit(df: pd.DataFrame) -> np.ndarray:
    """Charge below full [Ah] by coulomb counting, NaN until the first full sample
    of each pass. Full: in sunlight, charging, and within FULL_MARGIN_V of the
    pass's top voltage. When full, the surplus is dumped (D stays at 0)."""
    d = np.full(len(df), np.nan)
    for _, seg in df.groupby("segment"):
        idx = seg.index.to_numpy()
        v, inet, lit = seg.v_batt_v.to_numpy(), seg.i_net_a.to_numpy(), seg.lit_meas.to_numpy()
        top = np.percentile(v[lit], 90) if lit.any() else np.inf
        full = lit & (inet > 0) & (v >= top - FULL_MARGIN_V)
        cur = np.nan
        for j in range(len(seg)):
            if j > 0 and not np.isnan(cur):
                cur = max(0.0, cur - inet[j - 1] / 60)  # 1-minute steps, Ah
            if full[j]:
                cur = 0.0
            d[idx[j]] = cur
    return d


def battery_current(d: np.ndarray, i_net: np.ndarray) -> np.ndarray:
    """Current into the cells: the net bus current, except when the battery is
    full and the surplus is dumped."""
    return np.where((d <= 1e-9) & (i_net > 0), 0.0, i_net)


def battery_kf(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Kalman filter on theta = [V_full, k, R] with V = V_full - k D + R I.

    V_full follows a slow random walk (it moves with the cell temperature);
    k and R are nearly constant. Samples where D is unknown are skipped."""
    d = df.deficit_ah.to_numpy()
    i_b = battery_current(d, df.i_net_a.to_numpy())
    v = df.v_batt_v.to_numpy()
    x = np.array([v.max(), 2.0, R_INTERNAL_OHM])
    P = np.diag([0.05, 2.0, 0.1]) ** 2
    Q = np.diag([2e-4, 1e-4, 1e-5]) ** 2
    r = 0.006 ** 2 + 0.008 ** 2          # 10-mV quantisation plus model error
    n = len(df)
    est = np.full((n, 3), np.nan)
    sd = np.full((n, 3), np.nan)
    for k in range(n):
        P = P + Q
        if not np.isnan(d[k]):
            H = np.array([1.0, -d[k], i_b[k]])
            s = H @ P @ H + r
            K = P @ H / s
            x = x + K * (v[k] - H @ x)
            P = P - np.outer(K, H) @ P
        est[k], sd[k] = x, np.sqrt(np.diag(P))
    return {"v_full": est[:, 0], "k": est[:, 1], "r": est[:, 2],
            "v_full_sd": sd[:, 0], "k_sd": sd[:, 1], "r_sd": sd[:, 2]}


def capacity_from_slope(k_v_per_ah: float, v_full: float) -> tuple[float, float]:
    """Capacity [Ah, Wh] implied by the voltage drop per Ah, if the cells follow
    the typical Li-ion open-circuit curve of the twin at the full-charge voltage."""
    soc_full = np.interp(v_full / CELLS_IN_SERIES, OCV_CELL_V, OCV_SOC)
    slope = float(docv_dsoc(soc_full))          # V per unit SOC, whole pack
    ah = slope / k_v_per_ah
    return ah, ah * float(ocv(soc_full))


# ---------------------------------------------------------------- forward prediction

@dataclass
class Synced:
    array_factor: float
    load_sun_w: float
    load_eclipse_w: float
    v_full: float
    k: float
    r: float


def predict_synced(seg: pd.DataFrame, p: Synced) -> np.ndarray:
    """Battery voltage predicted from the orbit alone (twin sunlight and
    array power), starting from the first measured sample of the pass."""
    v = np.empty(len(seg))
    lit = seg.twin_sunlit.to_numpy()
    p_arr = p.array_factor * seg.p_twin_w.to_numpy()
    p_load = np.where(lit, p.load_sun_w, p.load_eclipse_w)
    v0, i0 = seg.v_batt_v.iloc[0], seg.i_net_a.iloc[0]
    d = max(0.0, (p.v_full + p.r * min(i0, 0) - v0) / p.k)
    v[0] = v0
    for j in range(1, len(seg)):
        i_net = (p_arr[j - 1] - p_load[j - 1]) / v[j - 1]
        d = max(0.0, d - i_net / 60)
        i_now = (p_arr[j] - p_load[j]) / v[j - 1]
        i_b = 0.0 if d <= 1e-9 and i_now > 0 else i_now
        # Constant-voltage charging: the regulator never lets the bus exceed V_full.
        v[j] = min(p.v_full, p.v_full - p.k * d + p.r * i_b)
    return v


def predict_datasheet(seg: pd.DataFrame, config: dict) -> np.ndarray:
    """The same prediction with the generic twin: datasheet battery (capacity,
    efficiencies, typical Li-ion curve up to 4.2 V per cell), as-designed array,
    constant orbit-average load."""
    bat = config["battery"]
    cap, ch, dis = bat["capacity_wh"], bat["charge_efficiency"], bat["discharge_efficiency"]
    load_w = average_load(config["loads"])
    p_arr = seg.p_twin_w.to_numpy()
    v0, i0 = seg.v_batt_v.iloc[0], seg.i_net_a.iloc[0]
    grid = np.linspace(0, 1, 201)
    soc = float(np.interp(v0 - R_INTERNAL_OHM * i0, ocv(grid), grid))
    v = np.empty(len(seg))
    v[0] = v0
    for j in range(1, len(seg)):
        net = p_arr[j - 1] - load_w
        e = soc * cap + (net * ch if net >= 0 else net / dis) / 60
        soc = min(max(e / cap, 0.0), 1.0)
        net_now = p_arr[j] - load_w
        i_b = 0.0 if soc >= 1 and net_now > 0 else net_now / float(ocv(soc))
        v[j] = float(ocv(soc)) + R_INTERNAL_OHM * i_b
    return v


# ---------------------------------------------------------------- pipeline

def run_real(path: Path = DATA, config_path: str = CONFIG, split: str = SPLIT) -> dict:
    """The whole alignment: returns the telemetry with twin columns and estimates,
    the calibrated parameters and the validation scores."""
    config = load_config(config_path)
    sat = satellite()
    df = load_telemetry(path)
    unix = unix_seconds(df.time)

    timing = eclipse_timing(df, sat)
    offset = timing["offset_s"]
    df["time_corr"] = df.time - pd.to_timedelta(offset, unit="s")
    df["twin_sunlit"] = twin_sunlit(sat, unix - offset)
    df["p_twin_w"] = twin_array_power(df.time_corr, df.twin_sunlit, config)

    # 2. Array factor: lit in both, away from eclipse edges.
    edge = (df.lit_meas != df.twin_sunlit) | (df.lit_meas != df.lit_meas.shift(fill_value=False))
    near_edge = edge.astype(int).rolling(2 * EDGE_GUARD_MIN + 1, center=True, min_periods=1).max().astype(bool)
    usable = df.lit_meas & df.twin_sunlit & ~near_edge
    ratio_sd = float((df.p_array_w[usable] / df.p_twin_w[usable]).std())
    y, h = df.p_array_w.to_numpy()[None], df.p_twin_w.to_numpy()[None]
    af, af_sd = scalar_kf(y, h, usable.to_numpy()[None], (ratio_sd * h) ** 2 + 1e-6,
                          q=(1e-4) ** 2, x0=1.0, p0=0.3 ** 2)
    df["array_factor"], df["array_factor_sd"] = af[0], af_sd[0]

    # 3. Loads in sunlight and in eclipse.
    for name, mask in (("sun", df.lit_meas & ~near_edge), ("eclipse", ~df.lit_meas & ~near_edge)):
        sd = float(df.p_load_w[mask].std())
        est, est_sd = scalar_kf(df.p_load_w.to_numpy()[None], np.ones((1, len(df))),
                                mask.to_numpy()[None], np.full((1, len(df)), sd ** 2),
                                q=(1e-4) ** 2, x0=average_load(config["loads"]), p0=0.5 ** 2)
        df[f"load_{name}_w"], df[f"load_{name}_sd"] = est[0], est_sd[0]

    # 4. Battery.
    df["deficit_ah"] = charge_deficit(df)
    for key, val in battery_kf(df).items():
        df[f"bat_{key}"] = val

    # Calibrated parameters: the filters' state at the split.
    split_t = pd.Timestamp(split)
    cal = df[df.time < split_t]
    last = cal.iloc[-1]
    synced = Synced(float(last.array_factor), float(last.load_sun_w), float(last.load_eclipse_w),
                    float(last.bat_v_full), float(last.bat_k), float(last.bat_r))
    cap_ah, cap_wh = capacity_from_slope(synced.k, synced.v_full)

    # Validation: predict each long pass after the split.
    df["v_pred_synced"] = np.nan
    df["v_pred_datasheet"] = np.nan
    for _, seg in df[df.time >= split_t].groupby("segment"):
        if len(seg) < MIN_SEGMENT_MIN:
            continue
        df.loc[seg.index, "v_pred_synced"] = predict_synced(seg, synced)
        df.loc[seg.index, "v_pred_datasheet"] = predict_datasheet(seg, config)
    val = df.dropna(subset=["v_pred_synced"])
    lit_both = val.lit_meas & val.twin_sunlit

    def rmse(a, b):
        return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))

    # Lowest voltage of each eclipse: the number an operator watches.
    mins = []
    for _, seg in val.groupby("segment"):
        dark = seg[~seg.lit_meas]
        if len(dark) >= 10:
            mins.append((dark.v_batt_v.min(), dark.v_pred_synced.min(), dark.v_pred_datasheet.min()))
    mins = np.array(mins) if mins else np.zeros((0, 3))

    durations = eclipse_durations(df, sat, offset)
    return {
        "df": df,
        "config": config,
        "timing": timing,
        "durations": durations,
        "synced": synced,
        "capacity_ah": cap_ah,
        "capacity_wh": cap_wh,
        "split": split_t,
        "scores": {
            "validation_passes": int(val.segment.nunique()),
            "validation_minutes": int(len(val)),
            "v_rmse_synced_mv": 1000 * rmse(val.v_batt_v, val.v_pred_synced),
            "v_rmse_datasheet_mv": 1000 * rmse(val.v_batt_v, val.v_pred_datasheet),
            "eclipse_min_err_synced_mv": float(1000 * np.mean(np.abs(mins[:, 1] - mins[:, 0]))) if len(mins) else None,
            "eclipse_min_err_datasheet_mv": float(1000 * np.mean(np.abs(mins[:, 2] - mins[:, 0]))) if len(mins) else None,
            "eclipses_validated": int(len(mins)),
            "p_array_rmse_synced_w": rmse(val.p_array_w[lit_both], synced.array_factor * val.p_twin_w[lit_both]),
            "p_array_rmse_datasheet_w": rmse(val.p_array_w[lit_both], val.p_twin_w[lit_both]),
            "sunlit_agreement": float((df.lit_meas == df.twin_sunlit).mean()),
        },
    }


def summary(res: dict) -> dict:
    """Headline numbers of the alignment, rounded for display."""
    df, s, t, sc = res["df"], res["synced"], res["timing"], res["scores"]
    cfg = res["config"]
    p_design = float(df.p_twin_w[df.twin_sunlit].mean())
    return {
        "satellite": "FUNcube-1 (AO-73), NORAD 39444",
        "source": "SatNOGS Network, whole-orbit telemetry decoded from raw frames",
        "first_sample": df.time.iloc[0].isoformat(),
        "last_sample": df.time.iloc[-1].isoformat(),
        "minutes": int(len(df)),
        "passes": int(df.segment.nunique()),
        "calibration_until": res["split"].isoformat(),
        "time_offset_s": round(t["offset_s"], 1),
        "eclipse_edge_scatter_s": round(t["edge_scatter_s"], 1),
        "eclipse_edges": t["n_entries"] + t["n_exits"],
        "eclipse_measured_min": round(res["durations"]["measured_min"], 2),
        "eclipse_predicted_min": round(res["durations"]["predicted_min"], 2),
        "eclipses_timed": res["durations"]["count"],
        "sunlit_agreement": round(sc["sunlit_agreement"], 4),
        "array_power_design_w": round(p_design, 2),
        "array_power_measured_w": round(float(df.p_array_w[df.lit_meas & df.twin_sunlit].mean()), 2),
        "array_factor": round(s.array_factor, 3),
        "array_factor_sd": round(float(df.array_factor_sd[df.time < res["split"]].iloc[-1]), 3),
        "load_datasheet_w": round(average_load(cfg["loads"]), 2),
        "load_sun_w": round(s.load_sun_w, 3),
        "load_eclipse_w": round(s.load_eclipse_w, 3),
        "battery_v_full_v": round(s.v_full, 3),
        "battery_k_v_per_ah": round(s.k, 2),
        "battery_r_ohm": round(s.r, 3),
        "battery_capacity_ah": round(res["capacity_ah"], 2),
        "battery_capacity_wh": round(res["capacity_wh"], 1),
        "battery_capacity_datasheet_wh": cfg["battery"]["capacity_wh"],
        "max_deficit_ah": round(float(np.nanmax(df.deficit_ah)), 3),
        **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in sc.items()},
    }


def plot(res: dict, path: str = "docs/real-vs-twin.png") -> None:
    """Twin vs real telemetry on one validation pass, plus the estimates over time."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df = res["df"]
    val = df.dropna(subset=["v_pred_synced"])
    # The validation pass with the longest eclipse coverage.
    seg_id = val.groupby("segment").apply(lambda s: (~s.lit_meas).sum() * 1000 + len(s)).idxmax()
    seg = df[df.segment == seg_id]
    t = (seg.time - seg.time.iloc[0]).dt.total_seconds() / 60

    fig, axes = plt.subplots(2, 2, figsize=(13, 7.5), constrained_layout=True)
    colors = {"real": "#1f2937", "datasheet": "#d97706", "synced": "#2563eb"}

    def shade(ax):
        dark = ~seg.twin_sunlit.to_numpy()
        for k in np.flatnonzero(np.diff(np.r_[0, dark.astype(int), 0]) == 1):
            end = k + np.argmax(~dark[k:]) if (~dark[k:]).any() else len(dark)
            ax.axvspan(t.iloc[k], t.iloc[min(end, len(t) - 1)], color="#cbd5e1", alpha=0.6, lw=0)

    ax = axes[0, 0]
    shade(ax)
    ax.plot(t, seg.p_array_w, color=colors["real"], lw=1.2, label="FUNcube-1 telemetry")
    ax.plot(t, seg.p_twin_w, color=colors["datasheet"], lw=1.6, ls="--", label="generic twin (as designed)")
    ax.plot(t, res["synced"].array_factor * seg.p_twin_w, color=colors["synced"], lw=1.8,
            label=f"synchronised twin (array factor {res['synced'].array_factor:.2f})")
    ax.set_title("Solar array power")
    ax.set_ylabel("W")
    ax.legend(fontsize=8, loc="lower left")

    ax = axes[0, 1]
    shade(ax)
    ax.plot(t, seg.v_batt_v, color=colors["real"], lw=1.2, label="FUNcube-1 telemetry")
    ax.plot(t, seg.v_pred_datasheet, color=colors["datasheet"], lw=1.6, ls="--", label="generic twin (datasheet)")
    ax.plot(t, seg.v_pred_synced, color=colors["synced"], lw=1.8, label="synchronised twin")
    ax.set_title("Battery voltage, predicted from the orbit alone")
    ax.set_ylabel("V")
    ax.legend(fontsize=8, loc="lower left")
    for a in axes[0]:
        a.set_xlabel(f"minutes from {seg.time.iloc[0]:%Y-%m-%d %H:%M} UTC (grey: predicted eclipse)")

    days = (df.time - df.time.iloc[0]).dt.total_seconds() / 86400
    split_d = (res["split"] - df.time.iloc[0]).total_seconds() / 86400
    ax = axes[1, 0]
    ax.plot(days, df.array_factor, color=colors["synced"], lw=1.5)
    ax.fill_between(days, df.array_factor - 2 * df.array_factor_sd, df.array_factor + 2 * df.array_factor_sd,
                    color=colors["synced"], alpha=0.2, lw=0)
    ax.axvline(split_d, color="#64748b", ls=":", lw=1)
    ax.set_ylim(0.6, 1.0)
    ax.set_title("Array factor (measured / as designed), Kalman estimate ±2σ")
    ax.set_xlabel(f"days from {df.time.iloc[0]:%Y-%m-%d}")

    ax = axes[1, 1]
    ah = np.array([capacity_from_slope(k, v)[0] if k > 0 else np.nan
                   for k, v in zip(df.bat_k, df.bat_v_full)])
    ax.plot(days, df.bat_r * 1000, color="#7c3aed", lw=1.5, label="internal resistance R [mΩ]")
    ax2 = ax.twinx()
    ax2.plot(days, ah * 1000, color="#059669", lw=1.5, label="capacity [mAh] (typical Li-ion curve)")
    ax.axvline(split_d, color="#64748b", ls=":", lw=1)
    ax.set_title("Battery: estimated, not measured on board")
    ax.set_xlabel(f"days from {df.time.iloc[0]:%Y-%m-%d} (dotted: end of calibration)")
    ax.set_ylim(150, 450)
    ax2.set_ylim(400, 800)
    ax.set_ylabel("mΩ", color="#7c3aed")
    ax2.set_ylabel("mAh", color="#059669")
    for a in fig.axes:
        a.grid(alpha=0.25)
    fig.suptitle("FUNcube-1 (AO-73): digital twin vs real telemetry from SatNOGS", fontsize=13)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    res = run_real()
    for key, value in summary(res).items():
        print(f"{key:>30}: {value}")
    plot(res)
    print("Saved docs/real-vs-twin.png")
