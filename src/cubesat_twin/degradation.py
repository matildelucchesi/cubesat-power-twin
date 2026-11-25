"""Solar array degradation of FUNcube-1 over the years, from SatNOGS telemetry.

The real-data mode (real.py) finds an array factor of about 0.77 in
November 2026, but over two weeks it cannot tell the design of the array
from its ageing. This module repeats the measurement on the same two weeks
of November in every year since SatNOGS keeps decoded frames (2021 to 2026)
and estimates the trend.

Besides ageing, three things change from one year to the next:

* **Earth-Sun distance**: removed by dividing by the irradiance of the day
  (the twin's as-designed power already includes it), and kept almost
  identical by using the same calendar dates every year.
* **Beta angle** (Sun direction vs orbit plane): FUNcube-1's orbit plane
  drifts with respect to the Sun, from 45 to 65 degrees in these Novembers.
  It changes how the Sun and the sunlit Earth (albedo) illuminate the
  panels. It turns out to be the dominant effect, so it is estimated
  together with the trend.
* **Temperature**: GaAs cells lose about 0.25 % of power per degree. The
  whole-orbit buffer reports four side-panel temperatures, and a 25 degC
  correction is computed. But within each year the daily power *rises*
  with panel temperature (about +1 %/degC): the temperature mostly tracks
  how much sunlight the panels get, not the cell efficiency. The correction
  is therefore reported as a sensitivity check, not used for the result.

Attitude makes the power swing minute by minute; whole days of sunlit
samples are averaged, each year is the median of its daily means (robust
to the few days with a different illumination regime), and its uncertainty
comes from the day-to-day scatter.

The trend is a Kalman filter on x = [level, rate per year, beta
coefficient], fed one year at a time. A systematic floor is added to each
year's variance so that the residuals are consistent with the
uncertainties (chi-square per degree of freedom of 1).

    python src/cubesat_twin/degradation.py   # summary + docs/array-degradation.png
"""
from pathlib import Path

import numpy as np
import pandas as pd

from power import SOLAR_CONSTANT_W_M2, effective_area, solar_irradiance
from simulation import load_config

DATA = Path("data/real/funcube1_history_daily.csv")
TLES = Path("data/real/funcube1_history_tles.txt")
CONFIG = "configs/cubesat_1u.yaml"

GAMMA_PER_C = -0.0025          # power temperature coefficient of triple-junction GaAs cells
T_REF_C = 25.0
SUN_CURRENT_MA = 20
EDGE_GUARD_MIN = 2
MIN_LIT_MINUTES_PER_DAY = 60

# Side-panel temperature calibrations of FUNcube-1 (raw 10-bit -> degC), as in gr-satellites.
PANEL_TEMP = {"temp_xp_raw": (-0.2073, 158.239), "temp_xm_raw": (-0.2083, 159.227),
              "temp_yp_raw": (-0.2076, 158.656), "temp_ym_raw": (-0.2087, 159.045)}


def daily_aggregates(raw: pd.DataFrame) -> pd.DataFrame:
    """Per-UTC-day means of the sunlit array power, raw and brought to 25 degC,
    from whole-orbit minutes (the CSV written by satnogs.py).

    Only sunlit samples away from eclipse edges are used (EDGE_GUARD_MIN on
    each side), so the partial illumination at entry and exit does not count.
    Years of minute data are large, so the repository keeps these daily
    aggregates; the same function rebuilds them from a fresh download."""
    time = pd.to_datetime(raw.time_utc, utc=True)
    p = raw.battery_mv / 1000 * raw.photocurrent_ma / 1000
    lit = raw.photocurrent_ma > SUN_CURRENT_MA
    seg = (time.diff().dt.total_seconds().fillna(1e9) > 61).cumsum()
    change = (lit != lit.groupby(seg).shift(fill_value=False)).astype(int)
    near = change.groupby(seg).transform(
        lambda s: s.rolling(2 * EDGE_GUARD_MIN + 1, center=True, min_periods=1).max()).astype(bool)
    temp = np.column_stack([a * raw[c] + b for c, (a, b) in PANEL_TEMP.items()]).mean(axis=1)
    use = (lit & ~near).to_numpy()
    df = pd.DataFrame({"date": time.dt.strftime("%Y-%m-%d"), "p": p, "temp": temp,
                       "p25": p / (1 + GAMMA_PER_C * (temp - T_REF_C))})[use]
    g = df.groupby("date")
    return pd.DataFrame({"minutes": g.size(), "p_array_w": g.p.mean(), "p_array_25c_w": g.p25.mean(),
                         "panel_temp_c": g.temp.mean()}).reset_index()


def load_history(path: Path = DATA, config_path: str = CONFIG) -> pd.DataFrame:
    """Daily aggregates with the as-designed power of the generic twin and the
    array factor, raw and corrected to 25 degC."""
    df = pd.read_csv(path)
    df = df[df.minutes >= MIN_LIT_MINUTES_PER_DAY].copy()
    df["time"] = pd.to_datetime(df.date, utc=True) + pd.Timedelta(hours=12)
    df["year"] = df.time.dt.year
    cfg = load_config(config_path)["solar_array"]
    area = effective_area(cfg["faces"], cfg["attitude"], cfg.get("deployable", 0.0))
    df["p_design_w"] = solar_irradiance(df.time) * cfg["cell_efficiency"] * area
    df["factor_raw"] = df.p_array_w / df.p_design_w
    df["factor_25c"] = df.p_array_25c_w / df.p_design_w
    return df.reset_index(drop=True)


def beta_angle_deg(line1: str, line2: str, times: pd.Series) -> float:
    """Mean beta angle (Sun direction vs orbit plane) over the given times."""
    from skyfield.api import EarthSatellite, load

    from eclipse import sun_direction

    ts = load.timescale()
    sat = EarthSatellite(line1, line2, "sat", ts)
    secs = (times - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy()
    t = ts.utc(1970, 1, 1, 0, 0, secs)
    geo = sat.at(t)
    h = np.cross(geo.position.km.T, geo.velocity.km_per_s.T)
    h /= np.linalg.norm(h, axis=1)[:, None]
    s = sun_direction(times.reset_index(drop=True))
    return float(np.degrees(np.arcsin(np.sum(h * s, axis=1))).mean())


def read_tles(path: Path = TLES) -> dict[int, tuple[str, str]]:
    """Year -> (line1, line2), from a file of 'year line1 line2' blocks."""
    out, lines = {}, [ln.rstrip() for ln in path.read_text().splitlines() if ln.strip()]
    for k in range(0, len(lines), 3):
        out[int(lines[k])] = (lines[k + 1], lines[k + 2])
    return out


BETA_REF_DEG = 55.0
MIN_DAYS_PER_YEAR = 5


def yearly(df: pd.DataFrame, column: str = "factor_raw") -> pd.DataFrame:
    """Per-year array factor: median of the daily means; uncertainty from their
    scatter (robust: 1.4826 x median absolute deviation)."""
    rows = {}
    for yr, g in df.groupby("year"):
        if len(g) < MIN_DAYS_PER_YEAR:
            continue
        v = g[column].to_numpy()
        mad = 1.4826 * np.median(np.abs(v - np.median(v)))
        t_mid = g.time.median()
        rows[yr] = {"factor": float(np.median(v)), "sd": 1.253 * mad / np.sqrt(len(v)),
                    "days": len(v), "minutes": int(g.minutes.sum()),
                    "panel_temp_c": float(g.panel_temp_c.median()),
                    "t_year": t_mid.year + (t_mid.dayofyear - 1) / 365.25,
                    "start": g.time.min(), "end": g.time.max()}
    return pd.DataFrame.from_dict(rows, orient="index")


def trend_kf(years: pd.DataFrame, use_beta: bool = True, sys_sd: float = 0.0) -> pd.DataFrame:
    """Kalman filter on x = [level at the first year, rate per year, beta coefficient],
    fed one year at a time: factor = level + rate * (t - t0) + k_beta * (beta - 55 deg).
    The state is constant (no process noise), so with all years in it equals the
    weighted least-squares fit; the rows show how the estimate converged."""
    t0 = years.t_year.iloc[0]
    x = np.array([years.factor.iloc[0], 0.0, 0.0])
    P = np.diag([0.2, 0.05, 0.01 if use_beta else 0.0]) ** 2
    rows = []
    for yr, r in years.iterrows():
        H = np.array([1.0, r.t_year - t0, (r.beta_deg - BETA_REF_DEG) if use_beta else 0.0])
        s = H @ P @ H + r.sd ** 2 + sys_sd ** 2
        K = P @ H / s
        x = x + K * (r.factor - H @ x)
        P = P - np.outer(K, H) @ P
        rows.append({"year": yr, "level": x[0], "rate_per_year": x[1], "k_beta_per_deg": x[2],
                     "level_sd": np.sqrt(P[0, 0]), "rate_sd": np.sqrt(P[1, 1]), "k_beta_sd": np.sqrt(P[2, 2]),
                     "rate_pct_per_year": 100 * x[1] / x[0], "rate_pct_sd": 100 * np.sqrt(P[1, 1]) / x[0]})
    return pd.DataFrame(rows).set_index("year")


def predict(years: pd.DataFrame, last: pd.Series, use_beta: bool = True) -> np.ndarray:
    t0 = years.t_year.iloc[0]
    beta = (years.beta_deg - BETA_REF_DEG) if use_beta else 0.0
    return last.level + last.rate_per_year * (years.t_year - t0) + last.k_beta_per_deg * beta


def fit(years: pd.DataFrame, use_beta: bool = True) -> tuple[pd.DataFrame, float]:
    """Trend with the systematic floor that makes chi2/dof = 1."""
    dof = len(years) - (3 if use_beta else 2)
    sys_sd = 0.0
    for _ in range(50):
        tr = trend_kf(years, use_beta, sys_sd)
        resid = years.factor - predict(years, tr.iloc[-1], use_beta)
        chi2 = float(np.sum(resid ** 2 / (years.sd ** 2 + sys_sd ** 2)))
        if dof <= 0 or chi2 <= dof:
            break
        sys_sd = max(sys_sd * 1.2, 0.001)
    return tr, sys_sd


def run_degradation(path: Path = DATA) -> dict:
    df = load_history(path)
    years = yearly(df)
    corr = yearly(df, "factor_25c")
    years["factor_25c"], years["sd_25c"] = corr.factor, corr.sd
    tles = read_tles()
    years["beta_deg"] = [beta_angle_deg(*tles[y], pd.Series(pd.date_range(r.start, r.end, freq="30min")))
                         for y, r in years.iterrows()]
    trend, sys_sd = fit(years)
    no_beta, sys_nb = fit(years, use_beta=False)
    with_temp, _ = fit(years.assign(factor=years.factor_25c, sd=years.sd_25c))
    # Within-year relation between daily factor and panel temperature (year offsets removed).
    d = df.assign(f=df.factor_raw - df.groupby("year").factor_raw.transform("mean"),
                  t=df.panel_temp_c - df.groupby("year").panel_temp_c.transform("mean"))
    temp_slope = float(np.polyfit(d.t, d.f, 1)[0] / df.factor_raw.mean())
    return {"df": df, "years": years, "trend": trend, "sys_sd": sys_sd,
            "no_beta": no_beta, "sys_sd_no_beta": sys_nb, "with_temp": with_temp,
            "within_year_temp_slope": temp_slope}


def summary(res: dict) -> dict:
    y, t = res["years"], res["trend"].iloc[-1]
    nb, wt = res["no_beta"].iloc[-1], res["with_temp"].iloc[-1]
    span = y.t_year.iloc[-1] - y.t_year.iloc[0]
    return {
        "years": [int(v) for v in y.index],
        "days_per_year": [int(v) for v in y.days],
        "factor_per_year": [round(float(v), 4) for v in y.factor],
        "factor_sd_per_year": [round(float(v), 4) for v in y.sd],
        "beta_deg_per_year": [round(float(v), 1) for v in y.beta_deg],
        "panel_temp_c_per_year": [round(float(v), 1) for v in y.panel_temp_c],
        "rate_pct_per_year": round(float(t.rate_pct_per_year), 2),
        "rate_pct_sd": round(float(t.rate_pct_sd), 2),
        "beta_effect_pct_per_deg": round(float(100 * t.k_beta_per_deg / t.level), 2),
        "beta_effect_pct_sd": round(float(100 * t.k_beta_sd / t.level), 2),
        "systematic_sd": round(res["sys_sd"], 4),
        "total_change_pct": round(float(t.rate_pct_per_year * span), 1),
        "total_change_pct_2sd": round(float(2 * t.rate_pct_sd * span), 1),
        "rate_pct_per_year_ignoring_beta": round(float(nb.rate_pct_per_year), 2),
        "rate_pct_sd_ignoring_beta": round(float(nb.rate_pct_sd), 2),
        "rate_pct_per_year_with_temp_correction": round(float(wt.rate_pct_per_year), 2),
        "within_year_power_vs_panel_temp_pct_per_c": round(100 * res["within_year_temp_slope"], 2),
    }


def plot(res: dict, path: str = "docs/array-degradation.png") -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    y, df, last = res["years"], res["df"], res["trend"].iloc[-1]
    sm = summary(res)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)

    ax = axes[0]
    beta_part = last.k_beta_per_deg * (y.beta_deg - BETA_REF_DEG)
    corrected = y.factor - beta_part
    tt = np.linspace(y.t_year.min() - 0.2, y.t_year.max() + 0.2, 50)
    line = last.level + last.rate_per_year * (tt - y.t_year.iloc[0])
    band = 2 * np.sqrt(last.level_sd ** 2 + (last.rate_sd * (tt - y.t_year.iloc[0])) ** 2)
    ax.fill_between(tt, line - band, line + band, color="#2563eb", alpha=0.15, lw=0)
    ax.plot(tt, line, color="#2563eb", lw=1.8,
            label=f"Kalman trend: {sm['rate_pct_per_year']:+.2f} ± {sm['rate_pct_sd']:.2f} % per year")
    err = 2 * np.sqrt(y.sd ** 2 + res["sys_sd"] ** 2)
    ax.errorbar(y.t_year - 0.03, y.factor, yerr=err, fmt="o", color="#d97706", ms=5, capsize=3,
                label="measured (median of daily means, ±2σ)")
    ax.errorbar(y.t_year + 0.03, corrected, yerr=err, fmt="o", color="#1f2937", ms=6, capsize=3,
                label=f"brought to beta = {BETA_REF_DEG:.0f}° ({sm['beta_effect_pct_per_deg']:+.2f} % per degree)")
    for yr, r in y.iterrows():
        ax.annotate(f"β {r.beta_deg:.0f}°", (r.t_year, r.factor), textcoords="offset points",
                    xytext=(6, 6), fontsize=8, color="#92400e")
    ax.set_xlabel("year (10–25 November of each year)")
    ax.set_ylabel("array factor (measured / as-designed generic 1U)")
    ax.set_title("FUNcube-1 solar array: ageing vs beta angle")
    ax.legend(fontsize=8, loc="lower left")

    ax = axes[1]
    for yr, g in df.groupby("year"):
        ax.scatter(g.panel_temp_c, g.factor_raw, s=14, label=str(yr))
    ax.set_xlabel("daily mean side-panel temperature [°C]")
    ax.set_ylabel("daily array factor")
    ax.set_title(f"Within a year power rises with temperature ({sm['within_year_power_vs_panel_temp_pct_per_c']:+.1f} %/°C):\n"
                 "panel temperature tracks illumination, not cell efficiency", fontsize=10)
    ax.legend(fontsize=8, ncol=3)
    for a in axes:
        a.grid(alpha=0.25)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    res = run_degradation()
    print(res["years"].drop(columns=["start", "end"]).round(4).to_string())
    print(res["trend"].round(4).to_string())
    for k, v in summary(res).items():
        print(f"{k:>40}: {v}")
    plot(res)
    print("Saved docs/array-degradation.png")
