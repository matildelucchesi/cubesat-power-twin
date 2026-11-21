"""REST API for the power digital twin (FastAPI).

Run from the project root:
    uvicorn api:app --app-dir src/cubesat_twin --reload
Interactive docs (OpenAPI): http://localhost:8000/docs
"""
import copy
from functools import lru_cache
from typing import Literal

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field

from eclipse import find_eclipses
from simulation import load_config, run, summary

CONFIG_PATH = "configs/cubesat_1u.yaml"
FACES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

app = FastAPI(title="CubeSat power digital twin",
              description="Orbit, eclipses, solar power and battery state of charge.")


class SimulationRequest(BaseModel):
    """Parameters the operator can change; everything else comes from the config."""
    days: float = Field(3, gt=0, le=14, description="Simulated time span [days]")
    step_s: int = Field(60, ge=10, le=600, description="Time step [s]")
    attitude: Literal["tumbling", "sun_pointing"] = "tumbling"
    failed_faces: list[Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"]] = []
    capacity_wh: float = Field(20.0, gt=0, description="Battery capacity [Wh]")
    initial_soc: float = Field(0.8, ge=0, le=1)
    tx_duty_cycle: float = Field(0.5, ge=0, le=1, description="Transmitter on-time fraction")
    eclipse_model: Literal["skyfield", "cylindrical"] = "skyfield"


@lru_cache
def base_config() -> dict:
    return load_config(CONFIG_PATH)


def build_config(req: SimulationRequest) -> dict:
    """Apply the request on top of the YAML config."""
    cfg = copy.deepcopy(base_config())
    cfg["simulation"].update(duration_days=req.days, step_s=req.step_s,
                             eclipse_model=req.eclipse_model)
    cfg["solar_array"]["attitude"] = req.attitude
    for face in req.failed_faces:
        cfg["solar_array"]["faces"][face] = 0.0
    cfg["battery"].update(capacity_wh=req.capacity_wh, initial_soc=req.initial_soc)
    cfg["loads"]["radio_tx_duty_cycle"] = req.tx_duty_cycle
    return cfg


@app.get("/api/config")
def get_config() -> dict:
    """The satellite configuration loaded from the YAML file."""
    return base_config()


@app.post("/api/simulate")
def simulate(req: SimulationRequest) -> dict:
    """Run a simulation and return summary figures, time series and the
    ground track of the first orbit day."""
    df = run(build_config(req))
    hours = (df.time - df.time.iloc[0]).dt.total_seconds() / 3600
    first_day = hours <= 24
    track_step = max(1, 60 // req.step_s)  # about one ground-track point per minute
    eclipses = find_eclipses(df.time, df.sunlit)

    return {
        "summary": summary(df),
        "series": {
            "hours": np.round(hours, 4).tolist(),
            "power_w": np.round(df.power_w, 3).tolist(),
            "load_w": np.round(df.load_w, 3).tolist(),
            "soc": np.round(df.soc, 4).tolist(),
            "sunlit": df.sunlit.astype(bool).tolist(),
        },
        "ground_track": {
            "lat": np.round(df.lat_deg[first_day][::track_step], 3).tolist(),
            "lon": np.round(df.lon_deg[first_day][::track_step], 3).tolist(),
        },
        "eclipses": [
            {"start": e.start.isoformat(), "duration_min": round(e.duration_min, 1)}
            for e in eclipses.head(20).itertuples()
        ],
    }
