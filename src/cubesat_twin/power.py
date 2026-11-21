"""Solar array power generation."""
import numpy as np
import pandas as pd

SOLAR_CONSTANT_W_M2 = 1361.0  # mean solar irradiance at 1 AU


def solar_irradiance(times: pd.Series) -> np.ndarray:
    """Solar irradiance [W/m^2], corrected for the Earth-Sun distance.

    The Earth is closest to the Sun in early January, so irradiance varies by
    about +/-3.3% over the year.
    """
    day_of_year = times.dt.day_of_year.to_numpy()
    return SOLAR_CONSTANT_W_M2 * (1 + 0.033 * np.cos(2 * np.pi * (day_of_year - 3) / 365.25))


def effective_area(faces: dict[str, float], attitude: str) -> float:
    """Cell area facing the Sun on average [m^2].

    - sun_pointing: the largest face points at the Sun (best case).
    - tumbling: random orientation. Averaged over all directions, a flat face
      sees the Sun with max(0, cos(angle)) = 1/4, so the mean area is the
      total cell area divided by 4.
    """
    areas = list(faces.values())
    if attitude == "sun_pointing":
        return max(areas)
    if attitude == "tumbling":
        return sum(areas) / 4
    raise ValueError(f"unknown attitude: {attitude}")


def generated_power(times: pd.Series, sunlit, faces: dict[str, float],
                    efficiency: float, attitude: str) -> np.ndarray:
    """Electrical power from the solar array [W] at each time step."""
    area = effective_area(faces, attitude)
    return np.asarray(sunlit, dtype=float) * solar_irradiance(times) * efficiency * area
