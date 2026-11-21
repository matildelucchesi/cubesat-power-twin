"""Eclipses: when the satellite is in the Earth's shadow.

Two models are compared:
- skyfield (is_sunlit), used as the reference, computed in orbit.propagate;
- a hand-written cylindrical shadow model, to make the physics explicit.
"""
import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6378.137  # WGS84 equatorial radius


def sun_direction(times: pd.Series) -> np.ndarray:
    """Unit vector from the Earth to the Sun in the equatorial frame (N x 3).

    Low-precision formula from the Astronomical Almanac: about 0.01 degrees
    of error, more than enough for eclipses.
    """
    jd = times.map(lambda t: t.timestamp()).to_numpy() / 86400.0 + 2440587.5
    d = jd - 2451545.0                                   # days since J2000
    mean_lon = np.radians(280.460 + 0.9856474 * d)       # mean longitude
    mean_anom = np.radians(357.528 + 0.9856003 * d)      # mean anomaly
    ecl_lon = (mean_lon + np.radians(1.915) * np.sin(mean_anom)
               + np.radians(0.020) * np.sin(2 * mean_anom))
    obliquity = np.radians(23.439 - 4e-7 * d)            # obliquity of the ecliptic
    return np.column_stack([
        np.cos(ecl_lon),
        np.cos(obliquity) * np.sin(ecl_lon),
        np.sin(obliquity) * np.sin(ecl_lon),
    ])


def sunlit_cylindrical(df: pd.DataFrame) -> np.ndarray:
    """True when the satellite is in sunlight, modelling the Earth's shadow
    as a cylinder.

    The satellite is in shadow when it is "behind" the Earth as seen from the
    Sun (negative projection on the Sun direction) and its distance from the
    Earth-Sun axis is smaller than the Earth's radius.
    """
    r = df[["x_km", "y_km", "z_km"]].to_numpy()
    s = sun_direction(df.time)
    along = np.sum(r * s, axis=1)                        # component along the Sun direction
    perp = np.linalg.norm(r - along[:, None] * s, axis=1)  # distance from the axis
    in_shadow = (along < 0) & (perp < EARTH_RADIUS_KM)
    return ~in_shadow


def find_eclipses(time: pd.Series, sunlit) -> pd.DataFrame:
    """List of complete eclipses: start, end and duration in minutes.

    Eclipses already in progress at the start or not finished at the end of
    the simulation are discarded, because their duration is unknown.
    """
    shadow = (~pd.Series(np.asarray(sunlit, dtype=bool))).astype(int)
    change = shadow.diff()
    starts = list(np.flatnonzero(change.to_numpy() == 1))
    ends = list(np.flatnonzero(change.to_numpy() == -1))

    if ends and starts and ends[0] < starts[0]:
        ends = ends[1:]                  # eclipse started before the simulation
    n = min(len(starts), len(ends))      # drop an eclipse that has not ended

    t = time.reset_index(drop=True)
    rows = [{
        "start": t[starts[i]],
        "end": t[ends[i]],
        "duration_min": (t[ends[i]] - t[starts[i]]).total_seconds() / 60,
    } for i in range(n)]
    return pd.DataFrame(rows, columns=["start", "end", "duration_min"])


if __name__ == "__main__":
    from datetime import datetime

    from skyfield.api import load

    from orbit import propagate, read_tle
    from plots import plot_sunlight

    eph = load("de421.bsp")  # JPL ephemeris, about 17 MB on first run
    name, l1, l2 = read_tle("data/tle/funcube1.tle")
    df = propagate(name, l1, l2, datetime(2026, 11, 21), 86400, 30, eph=eph)
    df["sunlit_cyl"] = sunlit_cylindrical(df)

    for label, column in [("skyfield", "sunlit"), ("cylindrical", "sunlit_cyl")]:
        ecl = find_eclipses(df.time, df[column])
        print(f"{label:>11}: {len(ecl)} eclipses, mean duration "
              f"{ecl.duration_min.mean():.1f} min, "
              f"{(~df[column]).mean():.1%} of the time in shadow")

    disagree = (df.sunlit != df.sunlit_cyl).mean()
    print(f"The two models disagree on {disagree:.2%} of the time steps")

    plot_sunlight(df, "results/sunlight.png", f"{name}: sunlight and shadow, first 6 hours")
    print("Saved results/sunlight.png")
