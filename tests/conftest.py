import sys
from pathlib import Path

# Make the modules in src/cubesat_twin importable from the tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "cubesat_twin"))
