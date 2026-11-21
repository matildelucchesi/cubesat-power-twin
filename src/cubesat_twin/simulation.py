"""End-to-end power simulation: orbit -> eclipses -> power -> battery."""
import sys
from datetime import datetime

import pandas as pd
import yaml

from battery import simulate_battery
from eclipse import find_eclipses, sunlit_cylindrical
from loads import load_profile
from orbit import propagate, read_tle
from power import generated_power


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run(config: dict) -> pd.DataFrame:
    """Run the simulation described by a config dict and return a DataFrame
    with orbit, sunlight, generated power, load and state of charge."""
    sim = config["simulation"]
    name, l1, l2 = read_tle(config["satellite"]["tle_file"])

    eph = None
    if sim.get("eclipse_model", "skyfield") == "skyfield":
        from skyfield.api import load
        eph = load("de421.bsp")

    df = propagate(name, l1, l2, datetime.fromisoformat(sim["start"]),
                   sim["duration_days"] * 86400, sim["step_s"], eph=eph)
    if eph is None:
        df["sunlit"] = sunlit_cylindrical(df)

    array = config["solar_array"]
    df["power_w"] = generated_power(df.time, df.sunlit, array["faces"],
                                    array["cell_efficiency"], array["attitude"])
    df["load_w"] = load_profile(len(df), config["loads"])

    bat = config["battery"]
    df["soc"] = simulate_battery(df.power_w, df.load_w, sim["step_s"],
                                 bat["capacity_wh"], bat["initial_soc"],
                                 bat["charge_efficiency"], bat["discharge_efficiency"])
    df.attrs["name"] = name
    return df


def summary(df: pd.DataFrame) -> dict:
    """Key figures of a simulation run."""
    eclipses = find_eclipses(df.time, df.sunlit)
    return {
        "satellite": df.attrs.get("name", ""),
        "days": round((df.time.iloc[-1] - df.time.iloc[0]).total_seconds() / 86400, 2),
        "eclipses": len(eclipses),
        "mean_eclipse_min": round(float(eclipses.duration_min.mean()), 1) if len(eclipses) else 0.0,
        "shadow_fraction": round(float((~df.sunlit.astype(bool)).mean()), 3),
        "mean_power_w": round(float(df.power_w.mean()), 2),
        "mean_load_w": round(float(df.load_w.mean()), 2),
        "min_soc": round(float(df.soc.min()), 3),
        "final_soc": round(float(df.soc.iloc[-1]), 3),
    }


if __name__ == "__main__":
    from plots import plot_power_soc

    config = load_config(sys.argv[1] if len(sys.argv) > 1 else "configs/cubesat_1u.yaml")
    df = run(config)
    for key, value in summary(df).items():
        print(f"{key:>17}: {value}")
    df.to_csv("results/simulation.csv", index=False)
    plot_power_soc(df, "results/power_soc.png", f"{df.attrs['name']}: power and battery")
    print("Saved results/simulation.csv and results/power_soc.png")
