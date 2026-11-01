import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WAREHOUSE = ROOT / "data" / "warehouse.db"


@pytest.fixture(scope="session", autouse=True)
def mock_db():
    if not WAREHOUSE.exists():
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_mock_db.py")], check=True)
    return WAREHOUSE
