"""End-to-end power simulation: orbit -> eclipses -> power -> battery."""
import sys
from datetime import datetime

import pandas as pd
import yaml

from battery import simulate_battery
from eclipse import find_eclipses, sunlit_cylindrical
from loads import average_load, load_profile, safe_mode_load
from safe_mode import simulate_with_safe_mode
from orbit import propagate, read_tle
from power import generated_power


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run(config: dict) -> pd.DataFrame:
    """Run the simulation described by a config dict and return a DataFrame
    with orbit, sunlight, generated power, load and state of charge."""
    sim = config["simulation"]
    sat = config["satellite"]
    if "tle_lines" in sat:  # already resolved (e.g. downloaded by the API)
        name, l1, l2 = sat["tle_lines"]
    else:
        name, l1, l2 = read_tle(sat["tle_file"])

    eph = None
    model = sim.get("eclipse_model", "skyfield")
    if model == "skyfield":
        from skyfield.api import load
        try:
            eph = load("de421.bsp")
        except OSError:
            # No ephemeris file and no network: fall back to the cylindrical model.
            model = "cylindrical"

    df = propagate(name, l1, l2, datetime.fromisoformat(sim["start"]),
                   sim["duration_days"] * 86400, sim["step_s"], eph=eph)
    if eph is None:
        df["sunlit"] = sunlit_cylindrical(df)

    array = config["solar_array"]
    df["power_w"] = generated_power(df.time, df.sunlit, array["faces"],
                                    array["cell_efficiency"], array["attitude"],
                                    array.get("deployable", 0.0))
    bat = config["battery"]
    safe = config.get("safe_mode", {})
    if safe.get("enabled"):
        df["soc"], df["load_w"], df["safe"] = simulate_with_safe_mode(
            df.power_w, average_load(config["loads"]), safe_mode_load(config["loads"]),
            sim["step_s"], bat["capacity_wh"], bat["initial_soc"],
            bat["charge_efficiency"], bat["discharge_efficiency"],
            safe.get("enter_soc", 0.4), safe.get("exit_soc", 0.6))
    else:
        df["load_w"] = load_profile(len(df), config["loads"])
        df["soc"] = simulate_battery(df.power_w, df.load_w, sim["step_s"],
                                     bat["capacity_wh"], bat["initial_soc"],
                                     bat["charge_efficiency"], bat["discharge_efficiency"])
        df["safe"] = False
    df.attrs["name"] = name
    df.attrs["eclipse_model"] = model
    return df


def summary(df: pd.DataFrame) -> dict:
    """Key figures of a simulation run."""
    eclipses = find_eclipses(df.time, df.sunlit)
    return {
        "satellite": df.attrs.get("name", ""),
        "eclipse_model": df.attrs.get("eclipse_model", ""),
        "days": round((df.time.iloc[-1] - df.time.iloc[0]).total_seconds() / 86400, 2),
        "eclipses": len(eclipses),
        "mean_eclipse_min": round(float(eclipses.duration_min.mean()), 1) if len(eclipses) else 0.0,
        "shadow_fraction": round(float((~df.sunlit.astype(bool)).mean()), 3),
        "mean_power_w": round(float(df.power_w.mean()), 2),
        "mean_load_w": round(float(df.load_w.mean()), 2),
        "min_soc": round(float(df.soc.min()), 3),
        "final_soc": round(float(df.soc.iloc[-1]), 3),
        "safe_fraction": round(float(df.safe.mean()), 3) if "safe" in df else 0.0,
        "safe_entries": int((df.safe.astype(int).diff() == 1).sum() + int(df.safe.iloc[0])) if "safe" in df else 0,
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
