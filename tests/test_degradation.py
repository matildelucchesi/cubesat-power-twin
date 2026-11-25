import numpy as np
import pandas as pd
import pytest

from degradation import daily_aggregates, run_degradation, summary, trend_kf
from funcube import parse_frame, wo_samples
from test_real import WO4_FRAME


def test_wo_samples_decode_side_panel_temperatures():
    for _, v in wo_samples(parse_frame(WO4_FRAME)):
        for k in ("temp_xp_raw", "temp_xm_raw", "temp_yp_raw", "temp_ym_raw"):
            assert 0 <= v[k] < 1024
            assert -40 < -0.208 * v[k] + 159 < 90       # calibrated to a plausible °C


def _minutes(n_lit, n_dark, ma=500, mv=8000, raw_temp=650):
    t = pd.date_range("2026-11-20T00:00Z", periods=n_lit + n_dark, freq="min")
    cur = [ma] * n_lit + [0] * n_dark
    return pd.DataFrame({"time_utc": t.strftime("%Y-%m-%dT%H:%MZ"), "photocurrent_ma": cur,
                         "battery_mv": mv, "temp_xp_raw": raw_temp, "temp_xm_raw": raw_temp,
                         "temp_yp_raw": raw_temp, "temp_ym_raw": raw_temp})


def test_daily_aggregates_skip_dark_and_eclipse_edges():
    d = daily_aggregates(_minutes(30, 20))
    assert len(d) == 1
    # 2 minutes before eclipse entry, and the start of the data segment (it may follow an eclipse exit)
    assert d.minutes[0] == 30 - 2 - 3
    assert d.p_array_w[0] == pytest.approx(8.0 * 0.5)


def test_trend_kf_recovers_a_known_rate_and_beta_effect():
    years = pd.DataFrame({"t_year": np.arange(2021, 2027) + 0.88,
                          "beta_deg": [65, 55, 48, 45, 51, 62]}, index=range(2021, 2027))
    years["factor"] = 0.80 * (1 - 0.01 * (years.t_year - years.t_year.iloc[0])) - 0.003 * (years.beta_deg - 55)
    years["sd"] = 1e-4
    last = trend_kf(years).iloc[-1]
    assert last.rate_pct_per_year == pytest.approx(-1.0, abs=0.05)
    assert last.k_beta_per_deg == pytest.approx(-0.003, abs=2e-4)


def test_history_trend_is_small_and_beta_dominated():
    s = summary(run_degradation())
    assert s["years"] == [2021, 2022, 2023, 2024, 2025, 2026]
    assert all(0.7 < f < 0.9 for f in s["factor_per_year"])
    assert -2 < s["rate_pct_per_year"] < 0.5 and s["rate_pct_sd"] > 0
    assert s["beta_effect_pct_per_deg"] < 0 and abs(s["beta_effect_pct_per_deg"]) > 2 * s["beta_effect_pct_sd"]
    assert s["within_year_power_vs_panel_temp_pct_per_c"] > 0
