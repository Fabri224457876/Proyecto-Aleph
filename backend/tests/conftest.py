import sys
from pathlib import Path

# Hace importable `_testdb` desde los conftest y tests de cada carpeta.
sys.path.insert(0, str(Path(__file__).parent))
