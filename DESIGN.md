# Design notes: CubeSat power digital twin

This file explains the design choices behind the twin: what was chosen, how it compares with
the usual alternatives, and where the results stop being valid. The README describes *what* the twin
does; this file is about *why* it is built this way. Every number below can be regenerated
with the commands in [§ 8](#8-how-to-reproduce).

## 1. The question

The twin is built around one operational question: **will the battery hold?**

I needed this twin for two reasons.

The first is the project I have started for the **Laboratory of Automatic Control** exam, where
I will work on the attitude control of a CubeSat. A twin of the power system is the ground that
project stands on, so I built it first.

The second is where the interest in the battery comes from. In my satellite simulator, which
simulates the migration of the TeraFlow SDN controller in a Starlink constellation of two
orbits, the controller has to be migrated before the satellite enters the Earth's shadow. That
made me ask what happens to the battery in the dark: the satellite does not switch off in
eclipse, so it has to live on what it stored in sunlight. I wanted to see how a battery really
behaves in that situation, and possibly use the answer to make that simulator more complete
in the future.

From there the step to a digital twin was natural. A simulator starts from datasheet values; a
twin stays aligned with the spacecraft's data and can say something about its state *now*:
whether the battery will reach safe mode, how soon, and which action (for example a lower
transmitter duty cycle) keeps it out of it.

## 2. Scope

**In scope:** the electrical power system of a CubeSat: solar arrays, loads, battery, the
eclipses that drive them, an on-board safe mode, and a health-monitoring layer (estimation,
detection, diagnosis, prognosis) on top of the twin.

**Out of scope, on purpose:**

- **Attitude dynamics.** Attitude is either a tumbling average (total cell area / 4) or ideal
  sun pointing. These two cases bracket reality for the power budget.
- **Thermal coupling.** No cell temperature in the simulator. On FUNcube-1 the side-panel
  temperature turned out to track illumination rather than cell efficiency (§ 5.2), so a
  naive temperature correction would have made things worse.
- **Battery ageing over years, Earth albedo, penumbra.** Second-order for a week-long
  energy balance; listed in § 6.
- **Ground-station passes and load scheduling.** Loads are orbit averages per mode, and safe
  mode sheds a fixed set of loads.

## 3. Architecture

All Python code is in `src/cubesat_twin/`. The blocks and what flows between them:

```
                 ┌──────────────────────── simulated mode ───────────────────────┐
TLE (CelesTrak,  │                                                               │
bundled fallback)├→ orbit.py (SGP4) → eclipse.py (DE421 or cylindrical shadow)    │
  tle.py         │        → power.py (irradiance × η × area × attitude)          │
                 │        → battery.py + loads.py + safe_mode.py → SOC over days │
                 └───────────────────────────────────────────────────────────────┘
                                        │ twin prediction (orbit-average power)
                                        ▼
telemetry.py (truth model + injected faults) ─→ estimation.py (EKF + Kalman filters)
                                        │ estimates ± σ: SOC, capacity, array factor, load bias
                                        ▼
                         detection.py: limit checks │ CUSUM on drifts │ random forest │ hybrid
                                        ▼
                         prognosis.py: Monte Carlo time to safe mode, duty-cycle options
                                        ▼
                         assistant.py: Claude explains the report, calls the what-if as a tool

real-data mode:
SatNOGS frames → satnogs.py → funcube.py (decoder) → real.py: eclipse timing, Kalman filters,
                                                     validation; degradation.py: 2021–2026 trend

api.py (FastAPI) ←→ web/ (Angular dashboard, Three.js 3D view)
```

`health.py` chains telemetry → estimation → detection → prognosis for one run;
`health_benchmark.py` runs it on hundreds of labelled runs.

## 4. Key decisions

For each decision: what was chosen, why, and how it compares with the usual alternatives.
The "Alternatives and trade-offs" items are a technical comparison made after the fact, not a
record of options tried during development. Where an alternative was actually run and
measured, the item says so and gives the number.

### 4.1 Orbit and eclipse

- **Chosen:** SGP4 on public TLEs (via skyfield), output every minute in the GCRS frame.
  Shadow from skyfield's `is_sunlit` with the JPL DE421 ephemeris when available, otherwise a
  cylindrical Earth shadow with the Sun direction from the *Astronomical Almanac* low-precision
  formula.
- **Alternatives and trade-offs:** a two-body Keplerian orbit would be simpler but ignores
  oblateness and drag, and is not what TLEs are fitted for; a numerical propagator with a full
  force model would be more accurate than SGP4 but needs a precise initial state that public
  data does not give. For the shadow, a conical model with penumbra would add the few seconds
  of partial shadow at each edge, below the 1-minute resolution of FUNcube-1's data.
- **Why:** TLEs are made for SGP4, so it is the only consistent way to use them (it includes
  oblateness and drag; error grows by a few km per day from the epoch, so every run starts at
  the epoch). The cylinder is kept because it needs no ephemeris file and has a closed-form
  check (eclipse length vs beta angle); `python src/cubesat_twin/eclipse.py` compares the two.
  On FUNcube-1 the cylinder agrees with the measured sun/shadow state on 99.2 % of the minutes.

### 4.2 Solar-array and battery models

- **Chosen (simulator):** per-face power `P = S(t) · η · A · max(0, cos θ)` with
  S = 1361 W/m² ± 3.3 % (Earth–Sun distance), η = 0.28 (triple-junction GaAs), whole 30.18 cm²
  cells per face. Battery as an energy balance with charge efficiency 0.90 and discharge
  efficiency 0.95, clipped at full charge (surplus dumped, as a shunt regulator does).
- **Chosen (truth model and estimation):** a Li-ion open-circuit-voltage curve (typical NMC,
  2 cells in series) with an internal resistance of 0.12 Ω, so that the voltage carries
  information about the state of charge.
- **Chosen (real data, FUNcube-1):** the energy-balance battery did not fit, because
  FUNcube-1's charger stops at a fixed voltage (≈ 7.88 V) and the battery is full for most of
  the orbit. The model was changed to a **charge deficit** D below full, from coulomb counting,
  with `V = V_full − k·D + R·I`.
- **Alternatives and trade-offs:** keeping the simulator's energy-balance battery on real
  data was run and rejected by the data: it keeps charging to 8.4 V in sunlight while the real
  battery sits at 7.88 V (163 mV RMS error against 34 mV, § 5.2). An equivalent-circuit model
  with RC branches would capture the voltage relaxation after eclipse, but with 1-minute
  samples and 10 mV resolution its time constants would be poorly constrained.
- **Why:** on FUNcube-1 the measured channels (bus voltage, array and system current) can
  constrain V_full, k and R, but not a full cell model.
- **Parameters calibrated from data (FUNcube-1, 10–17 November 2026):** array factor
  0.77 ± 0.004, load 1.72 W in sunlight and 1.15 W in eclipse, V_full = 7.88 V,
  R = 0.31 Ω, k = 2.6 V/Ah (≈ 610 mAh with the typical Li-ion curve), clock offset −21 s.

### 4.3 Keeping the twin aligned with telemetry: Kalman filters

Simulated mode (`estimation.py`), all filters run on a batch of scenarios at once:

| Filter | State | Measurement | Process noise Q (per 60 s step) | Measurement noise R |
| --- | --- | --- | --- | --- |
| Battery EKF | [SOC, 1/capacity] | battery voltage via `OCV(SOC) + R_int·I`; prediction by coulomb counting | diag(1e-6, (5e-4/C)²) | (10 mV noise)² + (10 mV model error)² |
| Array factor | scalar, random walk | measured / predicted array power, only in sunlight with the battery clearly below full (V_oc < OCV(0.97)) | (5e-4)² | (0.35 · P_twin)² + (0.02 W)²: tumbling makes the power swing about 35 % around the orbit average |
| Load bias | scalar, random walk | measured load − load expected from the reported modes | (1e-3)² | (0.02 W)² |

Initial uncertainty: SOC σ = 0.05 (initial SOC from the first voltage), capacity σ = 15 %,
array factor 1 ± 0.1, load bias 0 ± 0.2 W.

Real-data mode (`real.py`): a 3-state filter on [V_full, k, R] with
Q = diag(2e-4, 1e-4, 1e-5)², R = (6 mV)² + (8 mV)² (10 mV quantisation plus model error),
and scalar random-walk filters (Q = (1e-4)²) for the array factor and the two load levels.

- **How Q and R were chosen:** R from the sensor noise of each channel plus an explicit
  model-error term (the dominant one for the array is the tumbling swing, not the sensor).
  Q small, so that the parameters behave as slow random walks: V_full may move with
  temperature, k and R are nearly constant.
- **Why the array samples are filtered:** with a full battery the power system curtails the
  array to what the loads draw, so the measurement says nothing about what the array could
  give. Whole full-battery periods are skipped, so that the selection does not favour moments
  of low illumination. Without this selection the array factor comes out too low even on
  runs with no fault: a selection bias.
- **Why Kalman filters:** they estimate what no sensor measures (capacity, array factor, load
  bias), each with a standard deviation that the detector reuses (§ 4.4).
- **Alternatives and trade-offs:** re-initialising the twin from telemetry corrects the state
  but learns nothing about the parameters. An unscented Kalman filter or a particle filter
  handles stronger nonlinearities; here the only nonlinear measurement is the OCV curve, which
  is smooth and locally almost linear, so an EKF is enough and a particle filter would cost
  far more for a batch of hundreds of runs. Batch least squares on a sliding window would give
  similar parameter estimates but no recursive uncertainty to feed the CUSUM.

### 4.4 Detection: CUSUM normalised by Kalman uncertainty

- **Idea:** once per orbit (95.5 min windows), each parameter's drift from its mean over a
  calibration period (hours 12–48) is divided by `sqrt(σ_Kalman² + floor²)`. A drift counts
  only when it is larger than what the twin can resolve, so the same threshold works for
  channels with very different units and noise, and a filter that is still converging (large
  σ) does not raise alarms.
- **Channels and directions:** array factor down, battery capacity down, load bias up. The
  channel that alarms names the subsystem.
- **Parameters:** one-sided CUSUM with allowance k = 1σ and decision threshold h = 8σ;
  model-error floor 3 % on every channel.
- **How the values were chosen:** k, h and the floor were proposed by the coding agent during
  development, not derived from a formal criterion. They were then checked on the benchmark
  (1 % false alarms, counting nominal runs with any alarm and fault runs alarmed before onset)
  and with the sensitivity study below, which shows why they are a reasonable operating point.
- **Sensitivity** (`python src/cubesat_twin/cusum_sensitivity.py 60`, CUSUM alone, the same 150
  test runs, one parameter varied at a time):

  | k (h = 8, floor 3 %) | 0.5 | 0.75 | **1** | 1.5 | 2 |
  | --- | --- | --- | --- | --- | --- |
  | Detected | 68 % | 82 % | **78 %** | 66 % | 57 % |
  | False alarms | 24 % | 5 % | **1 %** | 0 % | 0 % |

  | h (k = 1, floor 3 %) | 4 | 6 | **8** | 12 | 16 |
  | --- | --- | --- | --- | --- | --- |
  | Detected | 82 % | 80 % | **78 %** | 76 % | 69 % |
  | False alarms | 3 % | 2 % | **1 %** | 1 % | 1 % |

  | Floor (k = 1, h = 8) | 0 | 1 % | 2 % | **3 %** | 5 % |
  | --- | --- | --- | --- | --- | --- |
  | Detected | 72 % | 77 % | 82 % | **78 %** | 67 % |
  | False alarms | 21 % | 15 % | 5 % | **1 %** | 0 % |

  The floor and k matter most. Without a floor (σ from the filter alone) false alarms rise to
  21 %: the Kalman σ only covers the noise the filter is told about, so it ends up smaller
  than the twin's model error (tumbling, curtailment, the truth model's richer physics) and
  harmless drifts look significant. k = 1 and a 3 % floor are the cheapest settings that bring
  false alarms to about 1 %; going further (k = 1.5, or a 5 % floor) removes the last false
  alarm but loses 11 to 12 points of detection. h is the least sensitive parameter: from 6 to
  12 the result barely moves. At low k the detection rate also drops, because alarms that fire
  before the fault onset count as false alarms, not detections. Median delays are not compared
  here, because each setting detects a different mix of fast and slow faults.
  Caveat: the study uses the same test runs as the headline result, so it shows the local
  trade-off, not an independent check.
- **Alternatives and trade-offs:** fixed limits on telemetry channels (battery voltage below
  OCV(45 %), load above 2.5 W) were run in the benchmark: 18 % detected, because slow faults
  never cross a limit in time (§ 5.1). The classifier alone was run too: it reacts sooner but
  raises 23 % false alarms. A plain z-score threshold on each window would react to single
  noisy orbits; CUSUM accumulates evidence, so a small persistent drift is caught and a single
  outlier is not. A two-sided CUSUM would also flag improvements (for example a rising array
  factor after a high-beta season), which are not faults for this question.

### 4.5 Classification: random forest

- **Features** (per orbit window, from the twin's estimates, not from raw telemetry):
  `array_z`, `array_slope_24h`, `array_slope_72h`, `array_spread_dev`, `capacity_z`,
  `capacity_slope_48h`, `load_z`, `array_dev`, `capacity_dev`. The `_z` features are the
  same normalised drifts as the CUSUM; slopes are least-squares trends per day;
  `array_spread_dev` is the change of the spread (std/mean) of measured over predicted array
  power, which is the signature of the array geometry under tumbling.
- **Classes:** `nominal`, `string_loss`, `array_degradation`, `capacity_fade`, `load_anomaly`.
- **Model:** scikit-learn `RandomForestClassifier`, 200 trees, max depth 10, min 5 samples per
  leaf, balanced class weights. An ML alarm needs P(fault) > 0.8 for 4 consecutive windows.
- **Hybrid:** the CUSUM decides *when*; at that moment the classifier decides *what*, averaging
  its probabilities over the last 4 windows and restricted to the subsystem the CUSUM pointed
  to when the two disagree.
- **Why the features come from the twin:** working on the twin's estimates rather than raw
  telemetry is what lets it separate a lost cell string (a step that also changes the tumbling
  spread) from gradual degradation (a ramp), which look the same to a threshold on one channel.
  Feature importances are saved in `results/health_benchmark.json`.
- **Why a random forest:** the input is a small tabular feature set (9 features) with
  nonlinear class boundaries (step against ramp), it needs no feature scaling, and it gives
  feature importances for free.
- **Training data:** 300 synthetic runs from the truth model, scored on 150 other runs (§ 5.1).
- **Alternatives and trade-offs:** logistic regression would be easier to interpret but
  needs hand-made interactions to separate a step from a ramp. Gradient boosting would likely
  give similar accuracy on 9 features with more tuning. A neural network on raw telemetry would
  need far more labelled data and would lose the physical meaning that the twin's estimates
  give the features. No other classifier was benchmarked.

### 4.6 Prognosis

- **What it does:** from the filters' current state (SOC, capacity, array factor, load bias,
  each with σ) and the recent trend of array factor and capacity (last 72 h, hourly means,
  with a floor of 0.4 %/day on the trend uncertainty), a Monte Carlo ensemble of 200 forward runs
  on the predicted orbit gives the distribution of the time left before safe mode.
- **Decision step:** the ensemble is rerun for transmitter duty cycles 50 / 25 / 10 / 0 %; the
  twin recommends the highest duty cycle whose probability of safe mode within the horizon is
  ≤ 10 %.

### 4.7 LLM operator assistant (Claude API)

- **What it does:** `POST /api/health/explain` gives the health report (without the long time
  series) to Claude, which explains what happened, how sure the twin is, how much time is left
  and what to do. It has one client tool, `run_what_if(tx_duty)`, which runs the twin's
  forecast. Plain Messages API loop, bounded to 4 rounds.
- **What it is not allowed to do:** the system prompt requires it to use only numbers from the
  report or from tool results, to check an action with `run_what_if` before recommending it,
  and not to use the simulation's ground truth that the report carries for evaluation.
- **Fallback:** without an Anthropic credential the endpoint returns a deterministic template
  explanation built from the same report.
- **How its answers are checked:** unit tests with a fake client check that the tool is called
  and its output passed back (`tests/test_health.py`).

## 5. Validation

### 5.1 Simulated-fault benchmark

`python src/cubesat_twin/health_benchmark.py 60`: the classifier is trained on 300 runs and
the four methods are scored on 150 other runs (different seeds, fault sizes and onset times;
12 days each, 1-minute steps).

| Method | Detected | False alarms | Median delay | Right subsystem | Right fault type |
| --- | --- | --- | --- | --- | --- |
| Limit checking | 18 % | 0 % | 1.2 h | 100 % | 100 % |
| Twin + CUSUM | 78 % | 1 % | 45.6 h | 98 % | - |
| Twin + ML | 76 % | 23 % | 27.4 h | 88 % | 82 % |
| **Twin + CUSUM + ML** | **78 %** | **1 %** | 45.6 h | **98 %** | **97 %** |

Limit checking reacts faster on what it sees, but it only sees the larger stuck-on loads: the
1U has a large power margin, so a degrading array or a fading battery never crosses a limit
in time. The hybrid's delay is dominated by slow faults (about 100 h for 1–4 %/day
degradation); a stuck load is caught in about 2 h, a lost string in 31 h (median).

**How faults are injected** (`telemetry.py`, ranges in `health.py`): onset uniform between
hour 54 and 48 h before the end of the run, magnitude uniform in

| Fault | What changes | Magnitude range |
| --- | --- | --- |
| `string_loss` | fraction of one face's cells lost at onset (face chosen by the seed) | 50–100 % |
| `array_degradation` | array output lost per day after onset | 1–4 %/day |
| `capacity_fade` | battery capacity lost per day after onset | 1–4 %/day |
| `load_anomaly` | constant extra load from onset (e.g. a heater stuck on) | 0.15–0.5 W |

The truth model is deliberately richer than the twin: real tumbling about a random axis at
1–4 °/s, cells 0–8 % below datasheet capacity, internal resistance 0.9–1.3 × datasheet,
transmitter switched in 10-minute blocks, hysteresis safe mode, array curtailment with a full
battery, and sensor noise on every channel. The faults are accelerated so that they show
within days.

### 5.2 Real telemetry: FUNcube-1

- **Own decoder** (`funcube.py`): FUNcube-1 sends a 256-byte frame every 5 s; frame types 0–11
  carry the whole-orbit buffer, one 23-byte bit-packed sample per minute for the last
  104 minutes. The decoder extracts the X/Y/Z panel voltages, array current, battery voltage,
  system current and temperatures, following the layout as implemented in gr-satellites.
  `satnogs.py` downloads the frames from SatNOGS Network and merges overlapping passes.
- **Calibration and validation:** filters run on 10–17 November 2026; the twin then predicts
  18–25 November from the orbit alone (plus the first voltage of each pass).

| 17 passes, 2,447 minutes not used for calibration | Generic twin | Synchronised twin |
| --- | --- | --- |
| Battery voltage, RMS error | 162.7 mV | **34.1 mV** |
| Lowest voltage of each eclipse, mean error (15 eclipses) | 154 mV | **32 mV** |
| Array power in sunlight, RMS error | 0.92 W | 0.42 W |

- **Array ageing 2021–2026** (`degradation.py`): same dates every November; Kalman trend on
  [level, rate, beta coefficient] gives −0.5 ± 0.4 %/yr (not significant) and 0.34 ± 0.10 %
  less output per degree of beta. A plain line without beta gives −0.3 ± 0.8 %/yr.

## 6. Limitations

- **The fault benchmark is self-referential.** Faults are injected into a truth model written
  by the same author as the detector, and the detector's thresholds were tuned on it. The
  benchmark shows that the method works as designed; it does not show how it would perform on
  a real spacecraft.
- **No real faults.** The FUNcube-1 validation covers nominal operation only: no fault was
  observed in the period, so on real data the twin's models and filters are validated, not
  its detection and diagnosis.
- **Safe-mode result is simulated.** With 3 of 6 panels failed and safe mode on, the battery
  minimum is 39 % with the default DE421 shadow (64 % of the week in safe mode) and 38.4 % with
  the cylindrical shadow (63 %), as in the README table. Either way it is a simulation result,
  not a measured value.
- **Real-data battery capacity depends on an assumed OCV curve**, since FUNcube-1's cell
  datasheet was not used; the clock offset is estimated, not known; samples are 1 minute
  apart with 10 mV and 1 mA resolution.
- **Attitude is not modelled**: on FUNcube-1 the array power swings ±15 % minute to minute and
  the twin only models the orbit average, which is most of the remaining array-power error.
- **Prognosis assumes a linear trend** of the degradation seen so far; a fault that starts
  after the last telemetry sample is not foreseen. It is evaluated on one synthetic case
  (3 %/day array degradation: forecast median 226 h, truth 216 h), not on a benchmark.
- **Ageing trend** rests on one two-week window per year (2025 has only 6 days) and a single
  geometric regressor.

## 7. Next steps

- Evaluate the detector on real mission telemetry with labelled anomalies, to break the
  self-referential benchmark.
- Extend the prognosis to a forecast of the state of charge N orbits ahead with its uncertainty,
  and score it on many runs, not one.
- Continuous monitoring of FUNcube-1: array factor and battery every week, so that the health
  layer runs on real telemetry instead of synthetic faults.

## 8. How to reproduce

Developed on Ubuntu (WSL 2), Python 3.10+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q                                            # unit and API tests (also run in CI)

python src/cubesat_twin/simulation.py                # 7-day power simulation
python src/cubesat_twin/eclipse.py                   # DE421 vs cylindrical shadow
python src/cubesat_twin/health_benchmark.py 60       # § 5.1: train on 300 runs, score on 150
python src/cubesat_twin/cusum_sensitivity.py 60      # § 4.4: CUSUM sensitivity to k, h, floor
python src/cubesat_twin/health.py array_degradation  # one fault end to end, with prognosis
python src/cubesat_twin/real.py                      # § 5.2: FUNcube-1 calibration and validation
python src/cubesat_twin/degradation.py               # § 5.2: array ageing 2021–2026
```

The FUNcube-1 data used above is bundled in `data/real/`, so no network is needed;
`python src/cubesat_twin/satnogs.py --since 2026-11-10` refreshes it from SatNOGS.
