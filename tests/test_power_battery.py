import numpy as np
import pandas as pd
import pytest

from battery import simulate_battery
from loads import average_load
from power import effective_area, generated_power, solar_irradiance

FACES = {f: 0.006 for f in ("+X", "-X", "+Y", "-Y", "+Z", "-Z")}


def test_irradiance_higher_in_january_than_july():
    t = pd.Series(pd.to_datetime(["2026-01-03", "2026-07-04"], utc=True))
    jan, jul = solar_irradiance(t)
    assert jan > jul
    assert jan == pytest.approx(1361 * 1.033, rel=1e-3)


def test_effective_area():
    assert effective_area(FACES, "tumbling") == pytest.approx(0.036 / 4)
    assert effective_area(FACES, "sun_pointing") == pytest.approx(0.006)
    assert effective_area(FACES, "tumbling", deployable=0.024) == pytest.approx(0.06 / 4)
    assert effective_area(FACES, "sun_pointing", deployable=0.024) == pytest.approx(0.03)
    with pytest.raises(ValueError):
        effective_area(FACES, "spinning")


def test_no_power_in_eclipse():
    t = pd.Series(pd.to_datetime(["2026-11-21", "2026-11-21"], utc=True))
    p = generated_power(t, [True, False], FACES, 0.28, "tumbling")
    assert p[0] > 0 and p[1] == 0


def test_average_load_weights_transmitter_by_duty_cycle():
    loads = {"obc": 0.5, "radio_tx": 2.0, "radio_tx_duty_cycle": 0.25}
    assert average_load(loads) == pytest.approx(1.0)


def test_battery_discharges_and_charges():
    # 1 hour at -1 W, then 1 hour at +1 W, on a 10 Wh battery starting at 50%
    p_in = np.r_[np.zeros(60), 2 * np.ones(60)]
    p_out = np.ones(120)
    soc = simulate_battery(p_in, p_out, 60, 10.0, 0.5, charge_eff=0.9, discharge_eff=0.95)
    assert soc[59] == pytest.approx(0.5 - 1 / 0.95 / 10)
    assert soc[-1] == pytest.approx(soc[59] + 0.9 / 10)


def test_battery_is_clipped():
    soc = simulate_battery(np.full(10, 100.0), np.zeros(10), 3600, 10.0, 0.9)
    assert soc.max() == 1.0
    soc = simulate_battery(np.zeros(10), np.full(10, 100.0), 3600, 10.0, 0.1)
    assert soc.min() == 0.0
