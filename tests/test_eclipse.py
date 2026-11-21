
import numpy as np
import pandas as pd


from eclipse import EARTH_RADIUS_KM, find_eclipses, sun_direction, sunlit_cylindrical


def _times(n, step_s=60):
    return pd.Series(pd.date_range("2026-11-21", periods=n, freq=f"{step_s}s", tz="UTC"))


def test_sun_direction_is_unit_vector():
    s = sun_direction(_times(10))
    assert np.allclose(np.linalg.norm(s, axis=1), 1.0)


def test_sun_direction_at_march_equinox_points_along_x():
    t = pd.Series(pd.to_datetime(["2026-03-20 14:46"], utc=True))
    s = sun_direction(t)[0]
    assert s[0] > 0.999  # at the equinox the Sun is almost on the x axis


def test_cylindrical_shadow_behind_and_beside_earth():
    t = _times(1)
    s = sun_direction(t)[0]
    r = EARTH_RADIUS_KM + 500
    side = np.cross(s, [0, 0, 1])
    side /= np.linalg.norm(side)
    positions = {"towards the Sun": r * s, "behind the Earth": -r * s, "to the side": r * side}
    expected = {"towards the Sun": True, "behind the Earth": False, "to the side": True}
    for label, pos in positions.items():
        df = pd.DataFrame({"time": t, "x_km": [pos[0]], "y_km": [pos[1]], "z_km": [pos[2]]})
        assert sunlit_cylindrical(df)[0] == expected[label], label


def test_find_eclipses_counts_only_complete_ones():
    # shadow at the start (dropped), one complete 3-minute eclipse, shadow at the end (dropped)
    sunlit = [False, True, True, False, False, False, True, True, False]
    ecl = find_eclipses(_times(len(sunlit)), sunlit)
    assert len(ecl) == 1
    assert ecl.duration_min.iloc[0] == 3


def test_find_eclipses_no_shadow():
    ecl = find_eclipses(_times(5), [True] * 5)
    assert ecl.empty
