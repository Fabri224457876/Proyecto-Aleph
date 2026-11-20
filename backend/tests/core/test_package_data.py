"""Los archivos de datos del paquete tienen que viajar en la instalación.

Las pruebas corren sobre el código fuente (instalación editable) y ahí todo está a mano; la imagen
de Docker instala el paquete de verdad (`pip install ./backend`) y solo entra lo que `pyproject.toml`
declara en `package-data`. Un dato que falta (`tlds.txt` faltó) rompe el despliegue, no las pruebas.
"""

import fnmatch
import tomllib
from pathlib import Path

import aleph

PACKAGE_DIR = Path(aleph.__file__).parent
PYPROJECT = PACKAGE_DIR.parent / "pyproject.toml"
CODE = {".py", ".pyc", ".pyo"}


def _data_files() -> list[Path]:
    return [p for p in PACKAGE_DIR.rglob("*")
            if p.is_file() and p.suffix not in CODE and "__pycache__" not in p.parts]


def test_every_data_file_is_declared_as_package_data():
    patterns = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]["*"]
    files = _data_files()
    assert files, "se esperaban archivos de datos (ATT&CK, TLD, pesos de MENARD, sitios)"
    missing = [p.relative_to(PACKAGE_DIR).as_posix() for p in files
               if not any(fnmatch.fnmatch(p.name, pattern) for pattern in patterns)]
    assert missing == [], (
        f"estos archivos no viajan en la instalación; agregá su patrón a [tool.setuptools.package-data]: {missing}")
