import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))  # so `import profinder_pipeline` works without installing the package
