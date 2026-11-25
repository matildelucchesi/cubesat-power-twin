import numpy as np
import pandas as pd
import pytest

from funcube import parse_frame, wo_sample_time, wo_samples
from real import capacity_from_slope, charge_deficit, load_telemetry, run_real, summary

# A real FUNcube-1 frame (type WO4) received by a SatNOGS station on
# 2026-11-20 at 14:35:12 UTC, as the spacecraft entered eclipse.
WO4_FRAME = bytes.fromhex(
    "830f910e200ff6017e1eb000db05c300001515161500000301f501020e96a4eaba6f332573a98b6c728331851d"
    "b81bd9090f0f928b8d3fe44392b7e3aa69db16910e3111610eac01031e9200ce9b79439497fdab29fb12950ed3"
    "0e690fbe003c1e6900e09ec96c9ea877aeaacb22a302450192023a00011e4000e0a369afa738f9b1eb8b3ab000"
    "fb0096009100001e4b0092a7e9faad396eb4ac3b52bc00b20086008000001e400092ac5a3cb249d2b6ecbb66c5"
    "00750064007500001e400092b08a7db66a2db92d3b82ce00640000000000001e360097b41abcba4a7cbaedab9a"
    "d600000000000000001e360092b76af3bd7ac3bcae1bb2de00000000000000")


def test_parse_frame_realtime():
    f = parse_frame(WO4_FRAME)
    assert f.wo_chunk == 4
    rt = f.realtime
    assert rt["battery_mv"] == 7856 and rt["photocurrent_ma"] == 382 and rt["system_ma"] == 219
    assert rt["eclipse"] == 0 and rt["safemode"] == 0


def test_whole_orbit_samples_show_eclipse_entry():
    samples = wo_samples(parse_frame(WO4_FRAME))
    assert [i for i, _ in samples] == list(range(27, 34))   # sample 26 straddles WO3/WO4
    currents = [v["photocurrent_ma"] for _, v in samples]
    volts = [v["battery_mv"] for _, v in samples]
    assert currents[0] == 60 and all(c <= 1 for c in currents[1:])
    assert volts[-1] < volts[0]                                # battery discharging in the dark


def test_wo_sample_time_newest_is_wo1_time():
    f = parse_frame(WO4_FRAME)
    t = 1_000_000.0
    assert wo_sample_time(t, f, 103) == t - 15                 # WO1 was sent 3 frames earlier
    assert wo_sample_time(t, f, 102) == t - 75


def test_parse_frame_rejects_other_data():
    assert parse_frame(WO4_FRAME[:200]) is None
    assert parse_frame(bytes([0x00]) + WO4_FRAME[1:]) is None  # engineering model id


def test_charge_deficit_counts_from_full():
    t = pd.date_range("2026-11-20", periods=6, freq="min", tz="UTC")
    df = pd.DataFrame({"time": t, "segment": 0,
                       "v_batt_v": [7.88, 7.88, 7.80, 7.78, 7.76, 7.80],
                       "i_net_a": [0.1, -0.15, -0.15, -0.15, 0.1, 0.1],
                       "lit_meas": [True, False, False, False, True, True]})
    d = charge_deficit(df)
    assert d[0] == 0 and d[1] == pytest.approx(0.0)
    assert d[2] == pytest.approx(0.15 / 60) and d[4] == pytest.approx(0.45 / 60)
    assert d[5] == pytest.approx(0.35 / 60)


def test_capacity_from_slope_scales_inversely():
    a, _ = capacity_from_slope(2.0, 7.88)
    b, _ = capacity_from_slope(4.0, 7.88)
    assert a == pytest.approx(2 * b)


@pytest.fixture(scope="module")
def real():
    return run_real()


def test_telemetry_snapshot_loads():
    df = load_telemetry()
    assert len(df) > 5000 and df.segment.nunique() > 100
    assert df.v_batt_v.between(7.0, 8.5).all()


def test_twin_matches_real_eclipses(real):
    s = summary(real)
    assert s["sunlit_agreement"] > 0.97
    assert abs(s["eclipse_measured_min"] - s["eclipse_predicted_min"]) < 1.5
    assert abs(s["time_offset_s"]) < 90


def test_synchronised_twin_beats_datasheet_twin(real):
    s = summary(real)
    assert 0.5 < s["array_factor"] < 1.0
    assert s["load_eclipse_w"] < s["load_sun_w"]           # beacon off in eclipse
    assert s["v_rmse_synced_mv"] < 0.5 * s["v_rmse_datasheet_mv"]
    assert s["p_array_rmse_synced_w"] < s["p_array_rmse_datasheet_w"]
    assert np.isfinite(s["battery_capacity_ah"]) and s["battery_r_ohm"] > 0
