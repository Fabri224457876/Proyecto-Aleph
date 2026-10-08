#!/usr/bin/env bash
# Levanta la demo de Aleph: base SQLite, administrador, caso ficticio y API en el puerto 8100.
#
#   scripts/dev.sh              # primera vez, o para seguir con los datos de la demo
#   scripts/dev.sh --reiniciar  # borra la base y los datos de la demo antes de sembrar
#   PUERTO=8100 scripts/dev.sh
#
# Las contraseñas se toman de ALEPH_ADMIN_PASSWORD y ALEPH_DEMO_PASSWORD. Si no están, el script las
# genera y las imprime al final. No se guardan en ningún archivo.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"
PYTHON="$RAIZ/.venv/bin/python"
PUERTO="${PUERTO:-8100}"
if [[ ! -x "$PYTHON" ]]; then
    echo "No hay entorno virtual en .venv. Creálo con: python3 -m venv .venv && .venv/bin/python -m pip install -e 'backend[dev]'" >&2
    exit 1
fi
# Antes de sembrar: si el puerto está ocupado, la API no podría levantar
if (exec 3<>"/dev/tcp/127.0.0.1/$PUERTO") 2>/dev/null; then
    exec 3>&- 3<&-
    echo "El puerto $PUERTO ya está en uso. Cerralo o elegí otro con PUERTO=<número>." >&2
    exit 1
fi

new_secret() {
    # Caracteres seguros para copiar y pegar, longitud fija
    "$PYTHON" -c 'import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))' "$1"
}

DATA_DIR="$RAIZ/data/demo"
DB_PATH="$DATA_DIR/aleph-demo.db"
if [[ "${1:-}" == "--reiniciar" ]]; then
    rm -rf "$DATA_DIR"
    echo "Se borraron los datos anteriores de la demo."
fi
mkdir -p "$DATA_DIR"

export ALEPH_ENV=dev
export ALEPH_DATABASE_URL="sqlite:///$DB_PATH"
export ALEPH_DATA_DIR="$DATA_DIR"
export PYTHONPATH="$RAIZ/backend"
export PYTHONUTF8=1
: "${ALEPH_SECRET_KEY:=$(new_secret 32)}"
export ALEPH_SECRET_KEY
: "${ALEPH_ADMIN_USERNAME:=admin}"
export ALEPH_ADMIN_USERNAME

GENERATED_ADMIN=""
ADMINS="$("$PYTHON" -m aleph.demo.estado | tail -n 1)"

# 1) Administrador (bootstrap), solo si la base todavía no tiene ninguno
if [[ "$ADMINS" == "0" ]]; then
    if [[ -z "${ALEPH_ADMIN_PASSWORD:-}" ]]; then
        ALEPH_ADMIN_PASSWORD="$(new_secret 12)"
        GENERATED_ADMIN="$ALEPH_ADMIN_PASSWORD"
    fi
    export ALEPH_ADMIN_PASSWORD
    echo "Creando el administrador '$ALEPH_ADMIN_USERNAME' (bootstrap)..."
    "$PYTHON" -m aleph.api.bootstrap
else
    echo "La base ya tiene un administrador: no se crea otro."
fi

# 2) Caso de demostración (ficticio) y usuarios de demo. Es idempotente.
"$PYTHON" -m aleph.demo.seed

# 3) Resumen y API
echo
if [[ -n "$GENERATED_ADMIN" ]]; then
    echo "Contraseña generada por el script para el administrador '$ALEPH_ADMIN_USERNAME': $GENERATED_ADMIN"
fi
echo "API de Aleph: http://127.0.0.1:$PUERTO (documentación: http://127.0.0.1:$PUERTO/docs)"
echo "Ctrl+C para detener."
exec "$PYTHON" -m uvicorn aleph.main:app --host 127.0.0.1 --port "$PUERTO"
