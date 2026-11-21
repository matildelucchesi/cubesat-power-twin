"""Eclissi: quando il satellite e' nell'ombra della Terra.

Due modelli a confronto:
- skyfield (is_sunlit), usato come riferimento, calcolato in orbit.propagate;
- un modello a ombra cilindrica scritto a mano, per capire la fisica.
"""
import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6378.137  # raggio equatoriale WGS84


def sun_direction(times: pd.Series) -> np.ndarray:
    """Versore Terra->Sole nel sistema equatoriale (N x 3).

    Formula a bassa precisione dell'Astronomical Almanac: errore di circa
    0,01 gradi, piu' che sufficiente per le eclissi.
    """
    jd = times.map(lambda t: t.timestamp()).to_numpy() / 86400.0 + 2440587.5
    d = jd - 2451545.0                                   # giorni dal J2000
    mean_lon = np.radians(280.460 + 0.9856474 * d)       # longitudine media
    mean_anom = np.radians(357.528 + 0.9856003 * d)      # anomalia media
    ecl_lon = (mean_lon + np.radians(1.915) * np.sin(mean_anom)
               + np.radians(0.020) * np.sin(2 * mean_anom))
    obliquity = np.radians(23.439 - 4e-7 * d)            # inclinazione asse terrestre
    return np.column_stack([
        np.cos(ecl_lon),
        np.cos(obliquity) * np.sin(ecl_lon),
        np.sin(obliquity) * np.sin(ecl_lon),
    ])


def sunlit_cylindrical(df: pd.DataFrame) -> np.ndarray:
    """True se il satellite e' al sole, con l'ombra della Terra come cilindro.

    Il satellite e' in ombra se sta "dietro" la Terra rispetto al Sole
    (proiezione sulla direzione del Sole negativa) e la sua distanza
    dall'asse Terra-Sole e' minore del raggio terrestre.
    """
    r = df[["x_km", "y_km", "z_km"]].to_numpy()
    s = sun_direction(df.time)
    along = np.sum(r * s, axis=1)                        # componente lungo il Sole
    perp = np.linalg.norm(r - along[:, None] * s, axis=1)  # distanza dall'asse
    in_shadow = (along < 0) & (perp < EARTH_RADIUS_KM)
    return ~in_shadow


def find_eclipses(time: pd.Series, sunlit) -> pd.DataFrame:
    """Elenco delle eclissi complete: inizio, fine, durata in minuti.

    Le eclissi gia' in corso all'inizio o non finite alla fine della
    simulazione vengono scartate, perche' la loro durata non e' nota.
    """
    shadow = (~pd.Series(np.asarray(sunlit, dtype=bool))).astype(int)
    change = shadow.diff()
    starts = list(np.flatnonzero(change.to_numpy() == 1))
    ends = list(np.flatnonzero(change.to_numpy() == -1))

    if ends and starts and ends[0] < starts[0]:
        ends = ends[1:]                  # eclissi iniziata prima della simulazione
    n = min(len(starts), len(ends))      # scarta un'eclissi non ancora finita

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

    eph = load("de421.bsp")  # effemeridi JPL, circa 17 MB al primo avvio
    name, l1, l2 = read_tle("data/tle/funcube1.tle")
    df = propagate(name, l1, l2, datetime(2026, 11, 21), 86400, 30, eph=eph)
    df["sunlit_cyl"] = sunlit_cylindrical(df)

    for label, column in [("skyfield", "sunlit"), ("cilindrico", "sunlit_cyl")]:
        ecl = find_eclipses(df.time, df[column])
        print(f"{label:>10}: {len(ecl)} eclissi, durata media "
              f"{ecl.duration_min.mean():.1f} min, "
              f"{(~df[column]).mean():.1%} del tempo in ombra")

    disagree = (df.sunlit != df.sunlit_cyl).mean()
    print(f"I due modelli sono in disaccordo nel {disagree:.2%} degli istanti")

    plot_sunlight(df, "results/sunlight.png", f"{name}: sole e ombra, prime 6 ore")
    print("Salvato results/sunlight.png")
