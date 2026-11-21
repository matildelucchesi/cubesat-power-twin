"""Orbital propagation with TLE + skyfield (SGP4)."""
from datetime import datetime

import numpy as np
import pandas as pd
from skyfield.api import EarthSatellite, load, wgs84


def read_tle(path: str) -> tuple[str, str, str]:
    """Legge un file TLE a 3 righe e restituisce (nome, riga1, riga2)."""
    with open(path, "r") as f:
        name = f.readline().strip()
        line1 = f.readline().strip()
        line2 = f.readline().strip()
    return name, line1, line2


def propagate(name: str, line1: str, line2: str,
              start: datetime, duration_s: float, step_s: float) -> pd.DataFrame:
    """Restituisce un DataFrame con colonne: time, lat_deg, lon_deg, alt_km."""
    ts = load.timescale()
    satellite = EarthSatellite(line1, line2, name, ts)
    seconds = np.arange(0, duration_s, step_s)
    t = ts.utc(start.year, start.month, start.day, start.hour, start.minute, seconds)
    satellite_position = satellite.at(t)
    subpoint = wgs84.subpoint_of(satellite_position)
    latitude = subpoint.latitude.degrees
    longitude = subpoint.longitude.degrees
    altitude = wgs84.height_of(satellite_position).km
    df = pd.DataFrame({
        "time": t.utc_datetime(),
        "lat_deg": latitude,
        "lon_deg": longitude,
        "alt_km": altitude
    })
    return df


if __name__ == "__main__":
    name, l1, l2 = read_tle("data/tle/funcube1.tle")
    df = propagate(name, l1, l2, datetime(2026, 11, 21), 86400, 30)
    print(df.head())
    print("Altitudine min/max [km]:", df.alt_km.min(), df.alt_km.max())