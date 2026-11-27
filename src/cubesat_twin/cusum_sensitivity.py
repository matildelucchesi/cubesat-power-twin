"""Sensitivity of the twin CUSUM detector to its three parameters.

    python src/cubesat_twin/cusum_sensitivity.py [n_per_kind]

Uses the same 150 test runs as health_benchmark.py (same seeds), varies one
parameter at a time around the defaults (k = 1, h = 8, floor = 3 % on every
channel) and reports detection rate, false-alarm rate and median delay of the
CUSUM alone. Writes results/cusum_sensitivity.json.
"""
import json
import sys
from pathlib import Path

import numpy as np

from detection import CUSUM_FLOOR, CUSUM_H, CUSUM_K, cusum_check
from health import base_config, random_scenarios, twin_run
from health_benchmark import build, score

RESULTS = Path("results")
K_VALUES = (0.5, 0.75, 1.0, 1.5, 2.0)
H_VALUES = (4.0, 6.0, 8.0, 12.0, 16.0)
FLOOR_VALUES = (0.0, 0.01, 0.02, 0.03, 0.05)


def test_set(n_per_kind: int, days: float, seed: int = 0):
    """The test runs of health_benchmark.run_benchmark: the training runs are
    drawn first from the same generator, so they are drawn here too."""
    rng = np.random.default_rng(seed)
    random_scenarios(n_per_kind, days, rng, seed_offset=1000)
    return random_scenarios(max(4, n_per_kind // 2), days, rng, seed_offset=5000)


def evaluate(feats, scenarios, k, h, floor) -> dict:
    alarms = []
    for f in feats:
        alarms += cusum_check(f, k=k, h=h, floor={c: floor for c in CUSUM_FLOOR})
    r = score(alarms, scenarios)
    return {"k": k, "h": h, "floor": floor, "detection_rate": r["detection_rate"],
            "false_alarm_rate": r["false_alarm_rate"], "median_delay_h": r["median_delay_h"]}


def run(n_per_kind: int = 60, days: float = 12) -> dict:
    config = base_config(days)
    df = twin_run(config)
    test = test_set(n_per_kind, days)
    feats, _ = build(config, df, test)
    floor0 = CUSUM_FLOOR["array"]
    return {
        "test_runs": len(test),
        "k": [evaluate(feats, test, k, CUSUM_H, floor0) for k in K_VALUES],
        "h": [evaluate(feats, test, CUSUM_K, h, floor0) for h in H_VALUES],
        "floor": [evaluate(feats, test, CUSUM_K, CUSUM_H, fl) for fl in FLOOR_VALUES],
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    report = run(n)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "cusum_sensitivity.json").write_text(json.dumps(report, indent=2))
    print(f"{report['test_runs']} test runs; defaults k = {CUSUM_K}, h = {CUSUM_H}, floor = {CUSUM_FLOOR['array']}\n")
    for name in ("k", "h", "floor"):
        print(f"| {name} | Detected | False alarms | Median delay |")
        print("| --- | --- | --- | --- |")
        for r in report[name]:
            d = "-" if r["median_delay_h"] is None else f"{r['median_delay_h']} h"
            print(f"| {r[name]:g} | {100 * r['detection_rate']:.0f} % | "
                  f"{100 * r['false_alarm_rate']:.0f} % | {d} |")
        print()
