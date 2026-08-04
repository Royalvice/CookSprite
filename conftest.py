import sys
from pathlib import Path

# Ensure the repo root (holding the backend/ workflow/ cli/ packages) is on the
# import path when pytest runs from anywhere.
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
