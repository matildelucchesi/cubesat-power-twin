import numpy as np
import pytest

from safe_mode import SafeModeController, simulate_with_safe_mode


def test_hysteresis():
    c = SafeModeController(0.4, 0.6)
    # Falling: stays nominal until below 0.4.
    assert [c.update(s) for s in (0.7, 0.5, 0.41, 0.39)] == [False, False, False, True]
    # Rising: stays in safe mode between the thresholds, leaves above 0.6.
    assert [c.update(s) for s in (0.45, 0.55, 0.6, 0.61)] == [True, True, True, False]


def test_invalid_thresholds():
    with pytest.raises(ValueError):
        SafeModeController(0.6, 0.4)


def test_safe_mode_prevents_flat_battery():
    # Negative energy balance: 1.4 W in sunlight-average vs 1.7 W load.
    n = 7 * 24 * 60  # one week at 1-minute steps
    p_in = np.full(n, 1.4)
    soc_open, _, _ = simulate_with_safe_mode(p_in, 1.7, 1.7, 60, 20, 0.8, enter_soc=0.0, exit_soc=0.01)
    soc, load, safe = simulate_with_safe_mode(p_in, 1.7, 1.2, 60, 20, 0.8)
    assert soc_open.min() == 0.0
    assert soc.min() > 0.35
    assert safe.any() and (load[safe] == 1.2).all()
