import sys
from pathlib import Path

# Make the p2p_poc package importable regardless of how pytest is invoked.
sys.path.insert(0, str(Path(__file__).parent))
