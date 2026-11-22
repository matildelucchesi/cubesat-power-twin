"""Benchmark of fault detection and diagnosis on synthetic labelled telemetry.

    python src/cubesat_twin/health_benchmark.py [n_per_kind]

Generates a training set and an independent test set of telemetry runs
(different random seeds, fault sizes and onset times), trains the classifier
on the first, and scores the three methods of detection.py on the second:

* detection rate: fault runs with an alarm after the fault started;
* false alarms: nominal runs with any alarm, or fault runs alarmed before onset;
* delay: hours from fault onset to the alarm (median);
* subsystem: the alarm points to the right subsystem (array, battery, loads);
* fault type: the alarm names the exact fault (only the ML method can).

Writes results/health_benchmark.json and the trained model in
results/models/diagnosis.joblib, which the API then reuses.
"""
import json
import sys
from pathlib import Path

import numpy as np

from detection import (FEATURES, SUBSYSTEM, cusum_check, hybrid_check, limit_check, ml_check, train_classifier,
                       window_labels)
from health import MODEL_PATH, analyse, base_config, random_scenarios, save_classifier, twin_run

RESULTS = Path("results")


def score(alarms, scenarios) -> dict:
    faults = [(a, s) for a, s in zip(alarms, scenarios) if s.kind != "nominal"]
    nominal = [a for a, s in zip(alarms, scenarios) if s.kind == "nominal"]
    early = sum(1 for a, s in faults if a.hour is not None and a.hour < s.onset_h)
    detected = [(a, s) for a, s in faults if a.hour is not None and a.hour >= s.onset_h]
    delays = [a.hour - s.onset_h for a, s in detected]
    sub_ok = [a.subsystem == SUBSYSTEM[s.kind] for a, s in detected]
    type_ok = [a.fault == s.kind for a, s in detected]
    per_kind = {}
    for kind in sorted({s.kind for _, s in faults}):
        ds = [(a, s) for a, s in detected if s.kind == kind]
        n_kind = sum(1 for _, s in faults if s.kind == kind)
        per_kind[kind] = {
            "detection_rate": round(len(ds) / n_kind, 3),
            "median_delay_h": round(float(np.median([a.hour - s.onset_h for a, s in ds])), 1) if ds else None,
            "type_accuracy": round(float(np.mean([a.fault == s.kind for a, s in ds])), 3) if ds else None,
        }
    return {
        "detection_rate": round(len(detected) / len(faults), 3),
        "false_alarm_rate": round((sum(a.hour is not None for a in nominal) + early)
                                  / (len(nominal) + len(faults)), 3),
        "median_delay_h": round(float(np.median(delays)), 1) if delays else None,
        "subsystem_accuracy": round(float(np.mean(sub_ok)), 3) if sub_ok else None,
        "type_accuracy": round(float(np.mean(type_ok)), 3) if type_ok else None,
        "per_kind": per_kind,
    }


def build(config, df, scenarios, batch: int = 40):
    feats, labels = [], []
    for i in range(0, len(scenarios), batch):
        part = scenarios[i:i + batch]
        _, _, f = analyse(df, config, part)
        feats.append(f)
        labels.append(window_labels(f, part))
    return feats, labels


def run_benchmark(n_per_kind: int = 30, days: float = 12, seed: int = 0) -> dict:
    config = base_config(days)
    df = twin_run(config)
    rng = np.random.default_rng(seed)
    train = random_scenarios(n_per_kind, days, rng, seed_offset=1000)
    test = random_scenarios(max(4, n_per_kind // 2), days, rng, seed_offset=5000)

    f_train, y_train = build(config, df, train)
    clf = train_classifier(f_train, y_train)

    f_test, _ = build(config, df, test)
    results = {"limit": [], "twin_cusum": [], "twin_ml": [], "twin_hybrid": []}
    for f in f_test:
        results["limit"] += limit_check(f)
        results["twin_cusum"] += cusum_check(f)
        ml, proba = ml_check(clf, f)
        results["twin_ml"] += ml
        results["twin_hybrid"] += hybrid_check(clf, f, proba)
    report = {
        "train_runs": len(train), "test_runs": len(test), "days": days,
        "methods": {m: score(a, test) for m, a in results.items()},
    }
    report["feature_importance"] = dict(zip(FEATURES, clf.feature_importances_.round(3).tolist()))
    return report, clf


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    report, clf = run_benchmark(n)
    save_classifier(clf)
    (RESULTS / "health_benchmark.json").write_text(json.dumps(report, indent=2))
    print(f"train runs {report['train_runs']}, test runs {report['test_runs']}, {report['days']} days each\n")
    print("| Method | Detected | False alarms | Median delay | Right subsystem | Right fault type |")
    print("| --- | --- | --- | --- | --- | --- |")
    names = {"limit": "Limit checking", "twin_cusum": "Twin + CUSUM", "twin_ml": "Twin + ML",
             "twin_hybrid": "Twin + CUSUM + ML"}
    for m, r in report["methods"].items():
        pct = lambda v: "-" if v is None else f"{100 * v:.0f} %"
        print(f"| {names[m]} | {pct(r['detection_rate'])} | {pct(r['false_alarm_rate'])} | "
              f"{'-' if r['median_delay_h'] is None else str(r['median_delay_h']) + ' h'} | "
              f"{pct(r['subsystem_accuracy'])} | {pct(r['type_accuracy'])} |")
    print("\nPer fault type:")
    for m, r in report["methods"].items():
        print(f"  {names[m]}: {r['per_kind']}")
    print("\nFeature importance:", report["feature_importance"])
    print(f"\nSaved results/health_benchmark.json and {MODEL_PATH}")
