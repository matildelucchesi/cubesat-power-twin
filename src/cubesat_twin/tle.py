"""Two-line elements: download from CelesTrak by NORAD number, with a local cache.

The bundled FUNcube-1 TLE keeps the twin usable offline.
"""
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad}&FORMAT=TLE"
CACHE_DIR = Path("data/tle")
BUNDLED = {39444: CACHE_DIR / "funcube1.tle"}
MAX_AGE_S = 24 * 3600  # CelesTrak updates element sets a few times a day

# Real orbits with clearly different inclinations, to compare eclipse seasons.
PRESETS = [
    {"norad": 39444, "name": "FUNcube-1 (AO-73)", "orbit": "Sun-synchronous, 97.8°"},
    {"norad": 25544, "name": "ISS", "orbit": "51.6°, where ISS-deployed CubeSats start"},
    {"norad": 20580, "name": "Hubble Space Telescope", "orbit": "Low inclination, 28.5°"},
]


class TleUnavailable(Exception):
    """No element set could be downloaded or found locally."""


@dataclass
class Tle:
    name: str
    line1: str
    line2: str
    source: str  # celestrak | cache | bundled

    @property
    def norad(self) -> int:
        return int(self.line1[2:7])

    @property
    def epoch(self) -> datetime:
        """Epoch from line 1, columns 19-32: two-digit year and fractional day of year."""
        yy = int(self.line1[18:20])
        day = float(self.line1[20:32])
        year = 2000 + yy if yy < 57 else 1900 + yy
        return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1)

    @property
    def inclination_deg(self) -> float:
        return float(self.line2[8:16])


def parse_tle(text: str, norad: int | None = None) -> tuple[str, str, str]:
    """Return (name, line1, line2) from 3-line TLE text, checking the format."""
    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 3 or not lines[1].startswith("1 ") or not lines[2].startswith("2 "):
        raise ValueError("not a 3-line TLE")
    name, l1, l2 = lines[0].strip(), lines[1], lines[2]
    if norad is not None and int(l1[2:7]) != norad:
        raise ValueError(f"TLE is for NORAD {int(l1[2:7])}, not {norad}")
    return name, l1, l2


def _download(norad: int, timeout: float) -> str:
    req = urllib.request.Request(CELESTRAK_URL.format(norad=norad),
                                 headers={"User-Agent": "cubesat-power-twin"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("ascii", errors="replace")


def get_tle(norad: int, timeout: float = 8.0, download=None) -> Tle:
    """Fresh cache -> CelesTrak -> stale cache -> bundled file, in that order."""
    cached = CACHE_DIR / f"{norad}.tle"
    if cached.exists() and time.time() - cached.stat().st_mtime < MAX_AGE_S:
        return Tle(*parse_tle(cached.read_text(), norad), source="cache")
    try:
        text = (download or _download)(norad, timeout)
        tle = Tle(*parse_tle(text, norad), source="celestrak")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(f"{tle.name}\n{tle.line1}\n{tle.line2}\n")
        return tle
    except (OSError, ValueError):
        pass
    if cached.exists():
        return Tle(*parse_tle(cached.read_text(), norad), source="cache")
    if norad in BUNDLED:
        return Tle(*parse_tle(BUNDLED[norad].read_text(), norad), source="bundled")
    raise TleUnavailable(f"No TLE for NORAD {norad}: CelesTrak not reachable and nothing cached.")
