"""Fixed, separate database selection for public screenshot sessions."""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DEMO_DB = ROOT / "data" / "demo" / "kotobalab_demo.db"


def is_demo_mode(args=None):
    return "--demo" in (sys.argv[1:] if args is None else args)
