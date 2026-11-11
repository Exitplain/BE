"""Locate static assets owned by the separate Exitplain/FE checkout."""
import os
from pathlib import Path

FRONTEND_ROOT = Path(os.environ.get(
    "DROPSPOT_FE_ROOT", str(Path(__file__).resolve().parents[2] / "FE")
)).expanduser().resolve()
