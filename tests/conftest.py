import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Make the modules in src/cubesat_twin importable from the tests, and run
# from the project root so relative paths (configs/, data/) resolve.
sys.path.insert(0, str(ROOT / "src" / "cubesat_twin"))
os.chdir(ROOT)
