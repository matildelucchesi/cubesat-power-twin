# CubeSat power digital twin

A Python digital twin of a CubeSat's electrical power system, flying the real orbit of
**FUNcube-1 (AO-73)**. It propagates the orbit from a TLE, finds the eclipses, computes the
solar array power and simulates the battery state of charge. A FastAPI backend and an
Angular dashboard let an operator run what-if scenarios such as failed solar panels or a
smaller battery.

![Dashboard](docs/dashboard.png)

The 3D view replays the first two orbits: the Earth lit by the Sun, the Earth's shadow
cylinder, the orbit coloured by sunlight, and the CubeSat with each face shaded by how
directly it faces the Sun. A side panel shows the cosine factor of every face, the array
power and the battery charge at the selected time.

![3D view](docs/orbit-3d.png)

## What it models

| Block | Module | Model |
| --- | --- | --- |
| Orbit | `orbit.py` | SGP4 propagation of a CelesTrak TLE with skyfield |
| Eclipses | `eclipse.py` | skyfield `is_sunlit` (reference) and a hand-written cylindrical shadow model |
| Solar power | `power.py` | Body-mounted panels; tumbling (mean area = total / 4) or sun-pointing attitude; irradiance corrected for Earth-Sun distance |
| Loads | `loads.py` | Orbit-average consumption per subsystem, transmitter weighted by duty cycle |
| Battery | `battery.py` | Energy balance with charge/discharge efficiencies, clipped between empty and full |
| Simulation | `simulation.py` | Chains the blocks, driven by `configs/cubesat_1u.yaml` |
| API | `api.py` | FastAPI, OpenAPI docs at `/docs` |
| Dashboard | `web/` | Angular, hand-drawn SVG charts, Three.js 3D view |

## Results (7 days from 2026-11-21, nominal configuration)

- 106 eclipses of about 19 minutes, 20% of the time in shadow. The orbit's beta angle is
  about 61°, so eclipses are much shorter than the 35-minute maximum for this altitude.
- Mean generated power 2.8 W against a 1.7 W load: the battery never drops below 80%.
- Losing four of the six panels brings the mean power down to 0.94 W, and a 5 Wh battery is
  empty within 6 hours (screenshot below). That is the case a safe-mode controller must handle.

![Failure scenario](docs/dashboard-failure.png)

## Run it

Developed on Ubuntu (WSL 2). Requires Python 3.10+ and Node 20+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q                                  # unit and API tests

python src/cubesat_twin/simulation.py      # 7-day run: summary, results/simulation.csv, results/power_soc.png
python src/cubesat_twin/eclipse.py         # skyfield vs cylindrical eclipse model
```

Dashboard (two terminals):

```bash
# 1. backend
uvicorn api:app --app-dir src/cubesat_twin --reload    # http://localhost:8000/docs

# 2. frontend
cd web && npm install && npx ng serve                  # http://localhost:4200
```

Get a fresh TLE (SGP4 is accurate only for a few days around the TLE epoch):

```bash
curl -o data/tle/funcube1.tle "https://celestrak.org/NORAD/elements/gp.php?CATNR=39444&FORMAT=TLE"
```

## Model limits

- The spacecraft parameters in `configs/cubesat_1u.yaml` are representative values for a
  1U CubeSat, not FUNcube-1 datasheet values.
- Attitude is either a tumbling average or ideal sun pointing; no attitude dynamics.
- No temperature effects, cell degradation, Earth albedo or battery voltage model.
- Loads are a constant average; no ground-station passes or operational modes yet.
- When the battery is empty the load is not shed: a safe-mode controller is the next step.

## Physics or machine learning?

The physics model comes first: orbit, eclipses and panel geometry are well known, and a
physical model explains its results and extrapolates to unseen cases such as a failed panel.
The uncertain parts (cell temperature, degradation, real attitude and consumption) are where
a data-driven correction trained on real telemetry could reduce the residual error. That
hybrid step is planned once telemetry is available.
