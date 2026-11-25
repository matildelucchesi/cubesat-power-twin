"""Real FUNcube-1 telemetry from the SatNOGS Network.

SatNOGS (https://network.satnogs.org) is the open network of amateur ground
stations run by the Libre Space Foundation. After each pass a station
uploads the demodulated frames it received; observations and frames are
public and need no account or API token.

This script downloads the frames of FUNcube-1 (NORAD 39444) received since
a given date, decodes them (funcube.py) and writes two CSV files:

* funcube1_wholeorbit.csv: one row per minute from the whole-orbit buffer
  (several passes cover the same minutes; duplicates are merged);
* funcube1_realtime.csv: one row per received frame (every 5 s during passes).

    python src/cubesat_twin/satnogs.py --since 2026-11-10 --out data/real

The SatNOGS DB telemetry API (db.satnogs.org) has no decoder for FUNcube-1
and needs a token, which is why the raw frames of the Network are used.
"""
import argparse
import csv
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from funcube import RT_FIELDS, WO_FIELDS, parse_frame, wo_sample_time, wo_samples

API = "https://network.satnogs.org/api/observations/?norad_cat_id={norad}&format=json"
HEADERS = {"User-Agent": "cubesat-power-twin"}
TIME_IN_NAME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d)")


def _get(url: str, timeout: float = 30) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Link", "")


def observations(norad: int, since: str) -> list[dict]:
    """Observations with demodulated data, newest first, back to `since` (ISO date)."""
    url, out = API.format(norad=norad), []
    while url:
        body, link = _get(url)
        page = json.loads(body)
        out += [o for o in page if o.get("demoddata")]
        if not page or page[-1]["start"] < since:
            break
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = m.group(1) if m else None
        time.sleep(0.3)  # be gentle with a volunteer-run service
    return [o for o in out if o["start"] >= since]


def frame_time(url: str) -> float:
    """Reception time encoded in the frame file name, e.g. data_123_2026-11-20T14-35-12."""
    stamp = TIME_IN_NAME.search(url).group(1)
    return datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc).timestamp()


def download_frames(obs: list[dict], cache: Path) -> list[tuple[int, float, bytes]]:
    """(observation id, reception time, raw bytes) of every frame; files are cached."""
    frames = []
    for o in obs:
        for d in o["demoddata"]:
            url = d["payload_demod"]
            path = cache / str(o["id"]) / url.rsplit("/", 1)[-1]
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(_get(url)[0])
            frames.append((o["id"], frame_time(url), path.read_bytes()))
    return frames


def merge_whole_orbit(frames: list[tuple[int, float, bytes]]) -> dict[int, tuple]:
    """Whole-orbit samples keyed by UTC minute.

    Different stations' clocks and the 5-s frame spacing make the
    reconstructed minute of a pass uncertain by one or two minutes. Each
    observation is therefore shifted by the whole number of minutes that
    best matches the samples already collected (identical values), before
    being merged."""
    by_obs: dict[int, list] = {}
    for oid, t, raw in frames:
        by_obs.setdefault(oid, []).append((t, raw))
    merged: dict[int, tuple] = {}
    for oid in sorted(by_obs, key=lambda k: min(t for t, _ in by_obs[k])):
        local: dict[int, tuple] = {}
        for t, raw in sorted(by_obs[oid]):
            f = parse_frame(raw)
            if f is None or f.wo_chunk is None:
                continue
            for i, values in wo_samples(f):
                minute = round(wo_sample_time(t, f, i) / 60)
                local.setdefault(minute, tuple(values[k] for k in WO_FIELDS))
        best = max(range(-3, 4), key=lambda d: (sum(merged.get(m + d) == v for m, v in local.items()), -abs(d)))
        for m, v in local.items():
            merged.setdefault(m + best, v)
    return merged


def write_csvs(frames, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    wo = merge_whole_orbit(frames)
    wo_path, rt_path = out / "funcube1_wholeorbit.csv", out / "funcube1_realtime.csv"
    with wo_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time_utc", *WO_FIELDS])
        for m in sorted(wo):
            w.writerow([datetime.fromtimestamp(m * 60, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), *wo[m]])
    rt = {}
    for _, t, raw in frames:
        f = parse_frame(raw)
        if f is not None:
            rt[t] = tuple(f.realtime[k] for k in RT_FIELDS)
    with rt_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time_utc", *RT_FIELDS])
        for t in sorted(rt):
            w.writerow([datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), *rt[t]])
    return wo_path, rt_path


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--norad", type=int, default=39444)
    p.add_argument("--since", required=True, help="ISO date, e.g. 2026-11-10")
    p.add_argument("--out", default="data/real")
    p.add_argument("--cache", default="data/real/frames")
    a = p.parse_args()
    obs = observations(a.norad, a.since)
    frames = download_frames(obs, Path(a.cache))
    paths = write_csvs(frames, Path(a.out))
    print(f"{len(obs)} observations, {len(frames)} frames -> {paths[0]}, {paths[1]}")
