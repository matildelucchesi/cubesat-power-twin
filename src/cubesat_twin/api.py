"""REST API for the power digital twin (FastAPI).

Run from the project root:
    uvicorn api:app --app-dir src/cubesat_twin --reload
Interactive docs (OpenAPI): http://localhost:8000/docs
"""
import copy
from functools import lru_cache
from typing import Literal

from datetime import timedelta

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from eclipse import find_eclipses, sun_direction
from simulation import load_config, run, summary
from tle import PRESETS, Tle, TleUnavailable, get_tle

CONFIG_PATH = "configs/cubesat_1u.yaml"
FORM_FACTORS_PATH = "configs/form_factors.yaml"
FACES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

app = FastAPI(title="CubeSat power digital twin",
              description="Orbit, eclipses, solar power and battery state of charge.")


class SimulationRequest(BaseModel):
    """Parameters the operator can change; everything else comes from the config."""
    norad: int = Field(39444, ge=1, le=999999, description="NORAD catalog number of the orbit to fly")
    form_factor: Literal["1U", "3U", "6U"] = "1U"
    deployables: bool = Field(False, description="Add deployable solar panels")
    days: float = Field(3, gt=0, le=14, description="Simulated time span [days]")
    step_s: int = Field(60, ge=10, le=600, description="Time step [s]")
    attitude: Literal["tumbling", "sun_pointing"] = "tumbling"
    failed_faces: list[Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"]] = []
    capacity_wh: float = Field(20.0, gt=0, description="Battery capacity [Wh]")
    initial_soc: float = Field(0.8, ge=0, le=1)
    tx_duty_cycle: float = Field(0.5, ge=0, le=1, description="Transmitter on-time fraction")
    eclipse_model: Literal["skyfield", "cylindrical"] = "skyfield"
    safe_mode: bool = Field(False, description="Enable the on-board safe-mode controller")
    safe_enter_soc: float = Field(0.4, ge=0, le=1, description="Enter safe mode below this SOC")
    safe_exit_soc: float = Field(0.6, ge=0, le=1, description="Leave safe mode above this SOC")

    @model_validator(mode="after")
    def thresholds_ordered(self):
        if self.safe_enter_soc >= self.safe_exit_soc:
            raise ValueError("safe_enter_soc must be lower than safe_exit_soc (hysteresis)")
        return self


@lru_cache
def base_config() -> dict:
    return load_config(CONFIG_PATH)


@lru_cache
def form_factors() -> dict:
    return load_config(FORM_FACTORS_PATH)


def build_config(req: SimulationRequest, tle: Tle) -> dict:
    """Apply the request on top of the YAML config. The simulation starts at
    the first whole hour after the TLE epoch, where SGP4 is most accurate."""
    cfg = copy.deepcopy(base_config())
    ff = form_factors()[req.form_factor]
    start = (tle.epoch + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    cfg["satellite"]["tle_lines"] = (tle.name, tle.line1, tle.line2)
    cfg["simulation"].update(start=start.isoformat(), duration_days=req.days, step_s=req.step_s,
                             eclipse_model=req.eclipse_model)
    cfg["solar_array"]["faces"] = dict(ff["faces"])
    cfg["solar_array"]["deployable"] = ff["deployable"] if req.deployables else 0.0
    cfg["solar_array"]["attitude"] = req.attitude
    cfg["loads"] = {**ff["loads"], "radio_tx_duty_cycle": req.tx_duty_cycle}
    cfg["safe_mode"] = {"enabled": req.safe_mode, "enter_soc": req.safe_enter_soc,
                        "exit_soc": req.safe_exit_soc}
    for face in req.failed_faces:
        cfg["solar_array"]["faces"][face] = 0.0
    cfg["battery"].update(capacity_wh=req.capacity_wh, initial_soc=req.initial_soc)
    return cfg


@app.get("/api/config")
def get_config() -> dict:
    """The satellite configuration loaded from the YAML file."""
    return base_config()


@app.get("/api/catalog")
def catalog() -> dict:
    """Orbit presets and spacecraft form factors the operator can choose from."""
    return {"satellites": PRESETS, "form_factors": form_factors()}


@app.post("/api/simulate")
def simulate(req: SimulationRequest) -> dict:
    """Run a simulation and return summary figures, time series and the
    ground track of the first orbit day."""
    try:
        tle = get_tle(req.norad)
    except TleUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e))
    cfg = build_config(req, tle)
    df = run(cfg)
    hours = (df.time - df.time.iloc[0]).dt.total_seconds() / 3600
    first_day = hours <= 24
    track_step = max(1, 60 // req.step_s)  # about one ground-track point per minute
    eclipses = find_eclipses(df.time, df.sunlit)

    # 3D view: the first ~3.2 hours (two orbits), about one point per minute.
    first_orbits = df[hours <= 3.2].iloc[::max(1, 60 // req.step_s)]
    sun = sun_direction(df.time.iloc[[0]])[0]

    ff = form_factors()[req.form_factor]
    return {
        "summary": summary(df),
        "spacecraft": {
            "norad": tle.norad,
            "tle_source": tle.source,
            "tle_epoch": tle.epoch.isoformat(),
            "inclination_deg": tle.inclination_deg,
            "form_factor": req.form_factor,
            "size_u": ff["size_u"],
            "deployables": req.deployables,
            "cell_area_m2": round(sum(cfg["solar_array"]["faces"].values()) + cfg["solar_array"]["deployable"], 4),
        },
        "series": {
            "hours": np.round(hours, 4).tolist(),
            "power_w": np.round(df.power_w, 3).tolist(),
            "load_w": np.round(df.load_w, 3).tolist(),
            "soc": np.round(df.soc, 4).tolist(),
            "sunlit": df.sunlit.astype(bool).tolist(),
            "safe": df.safe.astype(bool).tolist(),
        },
        "ground_track": {
            "lat": np.round(df.lat_deg[first_day][::track_step], 3).tolist(),
            "lon": np.round(df.lon_deg[first_day][::track_step], 3).tolist(),
        },
        "orbit3d": {
            "start": df.time.iloc[0].isoformat(),
            "hours": np.round((first_orbits.time - df.time.iloc[0]).dt.total_seconds() / 3600, 4).tolist(),
            "lat_deg": np.round(first_orbits.lat_deg, 3).tolist(),
            "lon_deg": np.round(first_orbits.lon_deg, 3).tolist(),
            "alt_km": np.round(first_orbits.alt_km, 1).tolist(),
            "x_km": np.round(first_orbits.x_km, 1).tolist(),
            "y_km": np.round(first_orbits.y_km, 1).tolist(),
            "z_km": np.round(first_orbits.z_km, 1).tolist(),
            "sunlit": first_orbits.sunlit.astype(bool).tolist(),
            "power_w": np.round(first_orbits.power_w, 3).tolist(),
            "soc": np.round(first_orbits.soc, 4).tolist(),
            "load_w": np.round(first_orbits.load_w, 3).tolist(),
            "safe": first_orbits.safe.astype(bool).tolist(),
            "sun_dir": np.round(sun, 5).tolist(),
        },
        "eclipses": [
            {"start": e.start.isoformat(), "duration_min": round(e.duration_min, 1)}
            for e in eclipses.head(20).itertuples()
        ],
    }
